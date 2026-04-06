# Copyright (c) 2025, reformiqo and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import flt, getdate
from frappe.query_builder.functions import Sum
from collections import defaultdict


def execute(filters=None):
    columns = get_columns()
    data = get_data(filters)
    return columns, data


def get_columns():
    return [
        # --- Identity ---
        {"label": _("Date"), "fieldname": "Date", "fieldtype": "Data", "width": 100},
        {"label": _("Container No"), "fieldname": "Container Number", "fieldtype": "Data", "width": 130},
        {"label": _("Item"), "fieldname": "Item", "fieldtype": "Data", "width": 120},
        {"label": _("Lot Number"), "fieldname": "Lot Number", "fieldtype": "Data", "width": 110},
        {"label": _("Grade"), "fieldname": "Grade", "fieldtype": "Data", "width": 90},
        {"label": _("Cone"), "fieldname": "Cone", "fieldtype": "Data", "width": 70},
        {"label": _("Merge No"), "fieldname": "Merge No", "fieldtype": "Data", "width": 100},
        # --- Specifications ---
        {"label": _("Pulp"), "fieldname": "Pulp", "fieldtype": "Data", "width": 90},
        {"label": _("Lusture"), "fieldname": "Lusture", "fieldtype": "Data", "width": 90},
        {"label": _("Glue"), "fieldname": "Glue", "fieldtype": "Data", "width": 90},
        # --- Stock ---
        {"label": _("Balance Qty"), "fieldname": "Balance", "fieldtype": "Data", "width": 110},
        {"label": _("Booked Qty"), "fieldname": "Buyer Qty", "fieldtype": "Data", "width": 100},
        {"label": _("Balance Box"), "fieldname": "Balance Box", "fieldtype": "Data", "width": 100},
        {"label": _("Total Booked"), "fieldname": "Booked Qty", "fieldtype": "Data", "width": 110},
        {"label": _("Available Qty"), "fieldname": "Available Qty", "fieldtype": "Data", "width": 110},
        # --- Booking Details ---
        {"label": _("Sales Order"), "fieldname": "Sales Order", "fieldtype": "Link", "options": "Sales Order", "width": 150},
        {"label": _("Buyer"), "fieldname": "Buyers", "fieldtype": "Data", "width": 180},
        {"label": _("Sales Person"), "fieldname": "Sales Person", "fieldtype": "Data", "width": 130},
        {"label": _("Lifting Terms"), "fieldname": "Lifting Terms", "fieldtype": "Data", "width": 110},
        # --- Additional Info ---
        {"label": _("Cross Section"), "fieldname": "Cross Section", "fieldtype": "Data", "width": 110},
        {"label": _("Production Date"), "fieldname": "Production Date", "fieldtype": "Data", "width": 120},
        {"label": _("Notes"), "fieldname": "Notes", "fieldtype": "Data", "width": 150},
        {"label": _("Location"), "fieldname": "Location", "fieldtype": "Data", "width": 100},
        {"label": _("Accepted Warehouse"), "fieldname": "Accepted Warehouse", "fieldtype": "Data", "width": 150},
        {"label": _("sort_order"), "fieldname": "sort_order", "fieldtype": "Int", "width": 0, "hidden": 1},
    ]


def strip_prefix(val):
    """Strip item specification prefix (e.g. 'SPEC-value' -> 'value')"""
    if val and "-" in str(val):
        return str(val).rsplit("-", 1)[-1]
    return val or ""


def get_batch_balances(batch_ids):
    """Get stock balance per batch from SLE + SBE, queried in chunks."""
    if not batch_ids:
        return {}

    balance_map = {}
    CHUNK = 2000

    SLE = frappe.qb.DocType("Stock Ledger Entry")
    SBE = frappe.qb.DocType("Serial and Batch Entry")
    SBB = frappe.qb.DocType("Serial and Batch Bundle")

    for i in range(0, len(batch_ids), CHUNK):
        chunk = batch_ids[i : i + CHUNK]

        # Direct SLE entries (older method - batch_no on SLE itself)
        rows = (
            frappe.qb.from_(SLE)
            .select(SLE.batch_no, Sum(SLE.actual_qty).as_("balance"))
            .where(SLE.docstatus == 1)
            .where(SLE.is_cancelled == 0)
            .where(SLE.batch_no.isin(chunk))
            .where(
                (SLE.serial_and_batch_bundle.isnull())
                | (SLE.serial_and_batch_bundle == "")
            )
            .groupby(SLE.batch_no)
        ).run(as_dict=True)

        for r in rows:
            balance_map[r.batch_no] = balance_map.get(r.batch_no, 0) + flt(r.balance)

        # Bundle SBE entries (ERPNext v15+ method)
        rows = (
            frappe.qb.from_(SBE)
            .inner_join(SBB)
            .on(SBB.name == SBE.parent)
            .select(SBE.batch_no, Sum(SBE.qty).as_("balance"))
            .where(SBB.docstatus == 1)
            .where(SBB.is_cancelled == 0)
            .where(SBE.batch_no.isin(chunk))
            .groupby(SBE.batch_no)
        ).run(as_dict=True)

        for r in rows:
            balance_map[r.batch_no] = balance_map.get(r.batch_no, 0) + flt(r.balance)

    return balance_map


def get_booked_quantities(batch_ids):
    """Get per-booking details per batch from submitted Sales Orders.

    Returns a dict: batch_id -> list of {booked_qty, buyer, sales_person, lifting_terms}
    Only includes bookings where remaining qty (qty - delivered_qty) > 0.
    """
    if not batch_ids:
        return {}

    booked_map = {}  # batch_id -> list of individual bookings
    CHUNK = 2000

    SOI = frappe.qb.DocType("Sales Order Item")
    SO = frappe.qb.DocType("Sales Order")

    # Step 1: Query bookings (SOI + SO) without Sales Team to avoid cartesian product
    so_names = set()
    for i in range(0, len(batch_ids), CHUNK):
        chunk = batch_ids[i : i + CHUNK]

        rows = (
            frappe.qb.from_(SOI)
            .inner_join(SO)
            .on(SO.name == SOI.parent)
            .select(
                SOI.custom_batch_no.as_("batch_no"),
                SO.name.as_("sales_order"),
                SOI.qty,
                SOI.delivered_qty,
                SO.customer_name,
                SO.custom_lifting_terms.as_("lifting_terms"),
            )
            .where(SO.docstatus == 1)
            .where(SO.status.isin(["To Deliver and Bill", "To Deliver", "To Bill", "Partially Delivered"]))
            .where(SOI.custom_batch_no.isin(chunk))
        ).run(as_dict=True)

        for r in rows:
            remaining = flt(r.qty) - flt(r.delivered_qty)
            if remaining <= 0:
                continue

            bid = r.batch_no
            if bid not in booked_map:
                booked_map[bid] = []

            so_names.add(r.sales_order)
            booked_map[bid].append({
                "booked_qty": remaining,
                "sales_order": r.sales_order or "",
                "buyer": r.customer_name or "",
                "sales_person": "",
                "lifting_terms": r.lifting_terms or "",
            })

    # Step 2: Fetch sales persons per Sales Order from Sales Team child table
    sales_person_map = {}  # SO name -> comma-separated sales persons
    if so_names:
        ST = frappe.qb.DocType("Sales Team")
        so_list = list(so_names)
        for i in range(0, len(so_list), CHUNK):
            chunk = so_list[i : i + CHUNK]
            st_rows = (
                frappe.qb.from_(ST)
                .select(ST.parent, ST.sales_person)
                .where(ST.parent.isin(chunk))
                .where(ST.parenttype == "Sales Order")
            ).run(as_dict=True)
            for st in st_rows:
                if st.parent not in sales_person_map:
                    sales_person_map[st.parent] = []
                if st.sales_person and st.sales_person not in sales_person_map[st.parent]:
                    sales_person_map[st.parent].append(st.sales_person)

    # Step 3: Assign sales person to each booking
    for bid, bookings in booked_map.items():
        for bk in bookings:
            so_id = bk.get("sales_order")
            persons = sales_person_map.get(so_id, [])
            bk["sales_person"] = ", ".join(persons) if persons else ""

    return booked_map


def get_data(filters=None):
    if not filters:
        filters = {}

    fdt = filters.get("fdt")
    tdt = filters.get("tdt")
    container = filters.get("container")
    lot_no = filters.get("lot_no")
    cone = filters.get("cone")
    company = filters.get("company")


    # Step 1: Query filtered batches
    Batch = frappe.qb.DocType("Batch")
    select_fields = [
        Batch.name.as_("batch_id"),
        Batch.item,
        Batch.custom_container_no.as_("container_no"),
        Batch.custom_lot_no.as_("lot_no"),
        Batch.custom_cone.as_("cone"),
        Batch.custom_pulp.as_("pulp"),
        Batch.custom_lusture.as_("lusture"),
        Batch.custom_glue.as_("glue"),
        Batch.custom_grade.as_("grade"),
        Batch.creation,
        Batch.batch_qty.as_("net_weight"),
        Batch.custom_merge_no.as_("merge_no"),
    ]

    query = (
        frappe.qb.from_(Batch)
        .select(*select_fields)
    )

    if fdt:
        query = query.where(Batch.creation >= fdt)
    if tdt:
        query = query.where(Batch.creation <= tdt)

    if container:
        query = query.where(Batch.custom_container_no == container)
    if lot_no:
        query = query.where(Batch.custom_lot_no == lot_no)
    if cone:
        query = query.where(Batch.custom_cone == cone)

    # Filter by company via Container doctype
    if company:
        Container = frappe.qb.DocType("Container")
        company_containers = (
            frappe.qb.from_(Container)
            .select(Container.container_no)
            .where(Container.docstatus == 1)
            .where(Container.company == company)
        ).run(pluck="container_no")
        if not company_containers:
            return []
        query = query.where(Batch.custom_container_no.isin(company_containers))

    batches = query.run(as_dict=True)
    if not batches:
        return []

    batch_ids = [b.batch_id for b in batches]

    # Step 2: Get stock balance per batch
    balance_map = get_batch_balances(batch_ids)

    # Step 2b: Get per-booking details per batch
    booked_map = get_booked_quantities(batch_ids)

    # Step 2c: Get cross_section, notes, warehouse from Container doctype
    container_keys = set()
    for b in batches:
        container_keys.add((b.container_no or "", b.lot_no or ""))
    container_info = {}  # (container_no, lot_no) -> {cross_section, notes, location, accepted_warehouse}
    if container_keys:
        Container = frappe.qb.DocType("Container")
        cont_nos = list(set(ck[0] for ck in container_keys if ck[0]))
        if cont_nos:
            cont_rows = (
                frappe.qb.from_(Container)
                .select(
                    Container.container_no,
                    Container.lot_no,
                    Container.cross_section,
                    Container.notes,
                    Container.warehouse,
                    Container.production_date,
                    Container.set_warehouse,
                )
                .where(Container.docstatus == 1)
                .where(Container.container_no.isin(cont_nos))
            ).run(as_dict=True)
            for cr in cont_rows:
                ck = (cr.container_no or "", cr.lot_no or "")
                if ck in container_keys:
                    container_info[ck] = {
                        "cross_section": cr.cross_section or "",
                        "notes": cr.notes or "",
                        "location": cr.warehouse or "",
                        "production_date": str(cr.production_date) if cr.production_date else "",
                        "accepted_warehouse": cr.set_warehouse or "",
                    }

    # Step 3: Aggregate by group key in Python
    groups = {}
    for b in batches:
        batch_date = getdate(b.creation)
        key = (
            batch_date,
            b.container_no or "",
            b.lot_no or "",
            b.cone or "",
            b.item or "",
            b.pulp or "",
            b.lusture or "",
            b.glue or "",
            b.grade or "",
        )

        if key not in groups:
            ci = container_info.get((b.container_no or "", b.lot_no or ""), {})
            groups[key] = {
                "batch_date": batch_date,
                "container_no": b.container_no or "",
                "item": b.item or "",
                "pulp": b.pulp or "",
                "lusture": b.lusture or "",
                "glue": b.glue or "",
                "grade": b.grade or "",
                "lot_no": b.lot_no or "",
                "cone": b.cone or "",
                "balance": 0.0,
                "balance_box": 0,
                "booked_qty": 0.0,
                "bookings": [],  # list of individual booking dicts
                "merge_no": b.merge_no or "",
                "cross_section": ci.get("cross_section", ""),
                "production_date": ci.get("production_date", ""),
                "notes": ci.get("notes", ""),
                "location": ci.get("location", ""),
                "accepted_warehouse": ci.get("accepted_warehouse", ""),
            }

        if flt(balance_map.get(b.batch_id, 0)) > 0:
            groups[key]["balance"] += flt(b.net_weight)
            groups[key]["balance_box"] += 1

        # Collect individual bookings for this batch, consolidate by sales order
        bk_list = booked_map.get(b.batch_id)
        if bk_list:
            for bk in bk_list:
                groups[key]["booked_qty"] += bk["booked_qty"]
                # Merge into existing booking if same sales order
                so_id = bk.get("sales_order", "")
                existing = None
                for eb in groups[key]["bookings"]:
                    if eb.get("sales_order") == so_id and so_id:
                        existing = eb
                        break
                if existing:
                    existing["booked_qty"] += bk["booked_qty"]
                else:
                    groups[key]["bookings"].append(dict(bk))

    # Step 4: Filter - cone > 0, balance_box > 0, balance > 0
    main_rows = []
    for g in groups.values():
        try:
            cone_num = int(g["cone"]) if g["cone"] else 0
        except (ValueError, TypeError):
            cone_num = 0

        if cone_num > 0 and g["balance_box"] > 0 and flt(g["balance"]) > 0:
            g["sort_order"] = 0
            g["report_date"] = g["batch_date"].strftime("%d/%m/%Y")
            g["available_qty"] = round(flt(g["balance"]) - flt(g["booked_qty"]), 2)
            main_rows.append(g)

    if not main_rows:
        return []

    # Step 5: Compute lot totals (sort_order=1)
    lot_groups = defaultdict(list)
    for row in main_rows:
        lot_key = (row["report_date"], row["container_no"], row["lot_no"])
        lot_groups[lot_key].append(row)

    lot_totals = []
    for (report_date, container_no, lot), rows in lot_groups.items():
        lot_totals.append(
            {
                "batch_date": rows[0]["batch_date"],
                "report_date": report_date,
                "container_no": container_no,
                "item": str(len(rows)),
                "pulp": "",
                "lusture": "",
                "glue": "Total:",
                "grade": "",
                "balance": round(sum(r["balance"] for r in rows), 2),
                "lot_no": lot,
                "balance_box": sum(r["balance_box"] for r in rows),
                "cone": "",
                "sort_order": 1,
                "booked_qty": round(sum(r["booked_qty"] for r in rows), 2),
                "available_qty": round(sum(r["available_qty"] for r in rows), 2),
                "bookings": [],
                "merge_no": "",
                "cross_section": "",
                "production_date": "",
                "notes": "",
                "location": "",
                "accepted_warehouse": "",
            }
        )

    # Step 6: Compute container totals (sort_order=2, only when multiple lots)
    container_groups = defaultdict(list)
    for row in main_rows:
        ck = row["container_no"]
        container_groups[ck].append(row)

    container_totals = []
    for container_no, rows in container_groups.items():
        lots = set(r["lot_no"] for r in rows)
        if len(lots) > 1:
            container_totals.append(
                {
                    "batch_date": max(r["batch_date"] for r in rows),
                    "report_date": "",
                    "container_no": container_no,
                    "item": str(len(rows)),
                    "pulp": "",
                    "lusture": "",
                    "glue": "Grand Total:",
                    "grade": "",
                    "balance": round(sum(r["balance"] for r in rows), 2),
                    "lot_no": "",
                    "balance_box": sum(r["balance_box"] for r in rows),
                    "cone": "",
                    "sort_order": 2,
                    "booked_qty": round(sum(r["booked_qty"] for r in rows), 2),
                    "available_qty": round(sum(r["available_qty"] for r in rows), 2),
                    "bookings": [],
                    "merge_no": "",
                    "cross_section": "",
                    "production_date": "",
                    "notes": "",
                    "location": "",
                    "accepted_warehouse": "",
                }
            )

    # Step 7: Combine and sort
    all_rows = main_rows + lot_totals + container_totals
    all_rows.sort(
        key=lambda r: (
            -r["batch_date"].toordinal(),
            r["container_no"],
            r["lot_no"] if r["lot_no"] else "\xff",
            r["sort_order"],
            r["cone"] or "",
        )
    )

    # Step 7b: Compute report-level grand total (only from detail rows, sort_order=0)
    report_total = {
        "balance": round(sum(r["balance"] for r in main_rows), 2),
        "balance_box": sum(r["balance_box"] for r in main_rows),
        "booked_qty": round(sum(r["booked_qty"] for r in main_rows), 2),
        "available_qty": round(sum(r["available_qty"] for r in main_rows), 2),
        "cone": sum(int(r["cone"]) if r["cone"] else 0 for r in main_rows),
    }

    # Step 8: Format output — expand booking rows
    result = []
    for row in all_rows:
        so = row["sort_order"]
        bookings = row.get("bookings", [])

        # Base output dict for this stock row
        base = {
            "Date": "" if so >= 1 else row["report_date"],
            "Container Number": "" if so >= 1 else row["container_no"],
            "Item": row["item"],
            "Pulp": strip_prefix(row["pulp"]) if so == 0 else row["pulp"],
            "Lusture": strip_prefix(row["lusture"]) if so == 0 else row["lusture"],
            "Glue": strip_prefix(row["glue"]) if so == 0 else row["glue"],
            "Grade": strip_prefix(row["grade"]) if so == 0 else row["grade"],
            "Balance": round(flt(row["balance"]), 2),
            "Lot Number": row["lot_no"],
            "Balance Box": row["balance_box"],
            "Cone": row["cone"],
            "Booked Qty": round(flt(row["booked_qty"]), 2),
            "Available Qty": round(flt(row.get("available_qty", 0)), 2),
            "Merge No": row.get("merge_no", "") if so == 0 else "",
            "Cross Section": row.get("cross_section", "") if so == 0 else "",
            "Production Date": row.get("production_date", "") if so == 0 else "",
            "Notes": row.get("notes", "") if so == 0 else "",
            "Location": row.get("location", "") if so == 0 else "",
            "Accepted Warehouse": row.get("accepted_warehouse", "") if so == 0 else "",
            "sort_order": so,
        }

        if so != 0 or not bookings:
            # Total/grand-total rows, or detail rows with no bookings
            base["Sales Order"] = ""
            base["Buyers"] = ""
            base["Sales Person"] = ""
            base["Buyer Qty"] = ""
            base["Lifting Terms"] = ""
            result.append(base)
        else:
            # Progressive available qty: start from balance, deduct each booking
            running_available = flt(row["balance"])

            # First booking row — include all stock data
            first = bookings[0]
            first_row = dict(base)
            running_available -= flt(first["booked_qty"])
            first_row["Available Qty"] = round(running_available, 2)
            first_row["Sales Order"] = first["sales_order"]
            first_row["Buyers"] = first["buyer"]
            first_row["Sales Person"] = first["sales_person"]
            first_row["Buyer Qty"] = round(flt(first["booked_qty"]), 2) if first["booked_qty"] else ""
            first_row["Lifting Terms"] = first["lifting_terms"]
            result.append(first_row)

            # Subsequent booking rows — show progressive available qty
            for bk in bookings[1:]:
                running_available -= flt(bk["booked_qty"])
                result.append({
                    "Date": "",
                    "Container Number": "",
                    "Item": "",
                    "Pulp": "",
                    "Lusture": "",
                    "Glue": "",
                    "Grade": "",
                    "Balance": "",
                    "Lot Number": "",
                    "Balance Box": "",
                    "Cone": "",
                    "Booked Qty": "",
                    "Available Qty": round(running_available, 2),
                    "Sales Order": bk["sales_order"],
                    "Buyers": bk["buyer"],
                    "Sales Person": bk["sales_person"],
                    "Buyer Qty": round(flt(bk["booked_qty"]), 2) if bk["booked_qty"] else "",
                    "Lifting Terms": bk["lifting_terms"],
                    "Merge No": "",
                    "Cross Section": "",
                    "Production Date": "",
                    "Notes": "",
                    "Location": "",
                    "Accepted Warehouse": "",
                    "sort_order": -1,  # sub-booking row
                })

    # Append report-level grand total row
    result.append({
        "Date": "",
        "Container Number": "",
        "Item": "<b>Total</b>",
        "Pulp": "",
        "Lusture": "",
        "Glue": "",
        "Grade": "",
        "Balance": report_total["balance"],
        "Lot Number": "",
        "Balance Box": report_total["balance_box"],
        "Cone": report_total["cone"],
        "Booked Qty": report_total["booked_qty"],
        "Available Qty": report_total["available_qty"],
        "Sales Order": "",
        "Buyers": "",
        "Sales Person": "",
        "Buyer Qty": "",
        "Lifting Terms": "",
        "Merge No": "",
        "Cross Section": "",
        "Production Date": "",
        "Notes": "",
        "Location": "",
        "Accepted Warehouse": "",
        "sort_order": 3,
    })

    return result
