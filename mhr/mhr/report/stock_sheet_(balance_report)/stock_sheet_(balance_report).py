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
        {"label": _("Date"), "fieldname": "Date", "fieldtype": "Data", "width": 120},
        {
            "label": _("Container Number"),
            "fieldname": "Container Number",
            "fieldtype": "Data",
            "width": 150,
        },
        {"label": _("Item"), "fieldname": "Item", "fieldtype": "Data", "width": 150},
        {"label": _("Pulp"), "fieldname": "Pulp", "fieldtype": "Data", "width": 100},
        {
            "label": _("Lusture"),
            "fieldname": "Lusture",
            "fieldtype": "Data",
            "width": 100,
        },
        {"label": _("Glue"), "fieldname": "Glue", "fieldtype": "Data", "width": 100},
        {"label": _("Grade"), "fieldname": "Grade", "fieldtype": "Data", "width": 100},
        {
            "label": _("Balance"),
            "fieldname": "Balance",
            "fieldtype": "Data",
            "width": 100,
        },
        {
            "label": _("Lot Number"),
            "fieldname": "Lot Number",
            "fieldtype": "Data",
            "width": 120,
        },
        {
            "label": _("Balance Box"),
            "fieldname": "Balance Box",
            "fieldtype": "Data",
            "width": 100,
        },
        {"label": _("Cone"), "fieldname": "Cone", "fieldtype": "Data", "width": 100},
        {
            "label": _("sort_order"),
            "fieldname": "sort_order",
            "fieldtype": "Int",
            "width": 0,
            "hidden": 1,
        },
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


def get_data(filters=None):
    if not filters:
        filters = {}

    fdt = filters.get("fdt")
    tdt = filters.get("tdt")
    container = filters.get("container")
    lot_no = filters.get("lot_no")
    cone = filters.get("cone")

    if not fdt or not tdt:
        frappe.throw(_("Please select From Date and To Date"))

    # Step 1: Query filtered batches
    Batch = frappe.qb.DocType("Batch")
    query = (
        frappe.qb.from_(Batch)
        .select(
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
        )
        .where(Batch.creation >= fdt)
        .where(Batch.creation <= tdt)
    )

    if container:
        query = query.where(Batch.custom_container_no == container)
    if lot_no:
        query = query.where(Batch.custom_lot_no == lot_no)
    if cone:
        query = query.where(Batch.custom_cone == cone)

    batches = query.run(as_dict=True)
    if not batches:
        return []

    batch_ids = [b.batch_id for b in batches]

    # Step 2: Get stock balance per batch
    balance_map = get_batch_balances(batch_ids)

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
            }

        if flt(balance_map.get(b.batch_id, 0)) > 0:
            groups[key]["balance"] += flt(b.net_weight)
            groups[key]["balance_box"] += 1

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
            }
        )

    # Step 6: Compute container totals (sort_order=2, only when multiple lots)
    container_groups = defaultdict(list)
    for row in main_rows:
        ck = (row["report_date"], row["container_no"])
        container_groups[ck].append(row)

    container_totals = []
    for (report_date, container_no), rows in container_groups.items():
        lots = set(r["lot_no"] for r in rows)
        if len(lots) > 1:
            container_totals.append(
                {
                    "batch_date": rows[0]["batch_date"],
                    "report_date": report_date,
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

    # Step 8: Format output
    result = []
    for row in all_rows:
        so = row["sort_order"]
        result.append(
            {
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
                "sort_order": so,
            }
        )

    return result
