# Copyright (c) 2025, reformiqo and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import getdate
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
            "label": _("IN Qty"),
            "fieldname": "IN Qty",
            "fieldtype": "Data",
            "width": 100,
        },
        {
            "label": _("OUT Qty"),
            "fieldname": "OUT Qty",
            "fieldtype": "Data",
            "width": 100,
        },
        {"label": _("Stock"), "fieldname": "Stock", "fieldtype": "Data", "width": 100},
        {
            "label": _("Lot Number"),
            "fieldname": "Lot Number",
            "fieldtype": "Data",
            "width": 120,
        },
        {
            "label": _("Total Box"),
            "fieldname": "Total Box",
            "fieldtype": "Data",
            "width": 100,
        },
        {
            "label": _("sort_order"),
            "fieldname": "sort_order",
            "fieldtype": "Int",
            "width": 0,
            "hidden": 1,
        },
    ]


def get_delivered_batch_ids(batch_ids):
    """Get set of batch IDs delivered via Delivery Notes (direct + bundle)."""
    if not batch_ids:
        return set()

    delivered = set()
    CHUNK = 2000

    DNI = frappe.qb.DocType("Delivery Note Item")
    DN = frappe.qb.DocType("Delivery Note")
    SBE = frappe.qb.DocType("Serial and Batch Entry")
    SBB = frappe.qb.DocType("Serial and Batch Bundle")

    for i in range(0, len(batch_ids), CHUNK):
        chunk = batch_ids[i : i + CHUNK]

        # Direct delivery (batch_no on DNI itself)
        rows = (
            frappe.qb.from_(DNI)
            .inner_join(DN)
            .on(DN.name == DNI.parent)
            .select(DNI.batch_no)
            .distinct()
            .where(DN.docstatus == 1)
            .where(DNI.batch_no.isin(chunk))
            .where(
                (DNI.serial_and_batch_bundle.isnull())
                | (DNI.serial_and_batch_bundle == "")
            )
        ).run(as_dict=True)

        for r in rows:
            delivered.add(r.batch_no)

        # Bundle delivery (via Serial and Batch Bundle)
        rows = (
            frappe.qb.from_(SBE)
            .inner_join(SBB)
            .on(SBB.name == SBE.parent)
            .inner_join(DNI)
            .on(DNI.serial_and_batch_bundle == SBB.name)
            .inner_join(DN)
            .on(DN.name == DNI.parent)
            .select(SBE.batch_no)
            .distinct()
            .where(DN.docstatus == 1)
            .where(SBB.docstatus == 1)
            .where(SBB.is_cancelled == 0)
            .where(SBE.batch_no.isin(chunk))
        ).run(as_dict=True)

        for r in rows:
            delivered.add(r.batch_no)

    return delivered


def get_data(filters=None):
    if not filters:
        filters = {}

    fdt = filters.get("fdt")
    tdt = filters.get("tdt")
    container = filters.get("container")
    lot_no = filters.get("lot_no")

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
            Batch.custom_pulp.as_("pulp"),
            Batch.custom_lusture.as_("lusture"),
            Batch.custom_glue.as_("glue"),
            Batch.custom_grade.as_("grade"),
            Batch.creation,
        )
        .where(Batch.creation >= fdt)
        .where(Batch.creation <= tdt)
    )

    if container:
        query = query.where(Batch.custom_container_no == container)
    if lot_no:
        query = query.where(Batch.custom_lot_no == lot_no)

    batches = query.run(as_dict=True)
    if not batches:
        return []

    batch_ids = [b.batch_id for b in batches]

    # Step 2: Get set of delivered batch IDs
    delivered_set = get_delivered_batch_ids(batch_ids)

    # Step 3: Aggregate by group key (no cone/date grouping)
    groups = {}
    for b in batches:
        batch_date = getdate(b.creation)
        key = (
            b.container_no or "",
            b.lot_no or "",
            b.item or "",
            b.pulp or "",
            b.lusture or "",
            b.glue or "",
            b.grade or "",
        )

        if key not in groups:
            groups[key] = {
                "min_date": batch_date,
                "container_no": b.container_no or "",
                "item": b.item or "",
                "pulp": b.pulp or "",
                "lusture": b.lusture or "",
                "glue": b.glue or "",
                "grade": b.grade or "",
                "lot_no": b.lot_no or "",
                "batch_ids": set(),
            }

        if batch_date < groups[key]["min_date"]:
            groups[key]["min_date"] = batch_date

        groups[key]["batch_ids"].add(b.batch_id)

    # Step 4: Build main data rows
    main_rows = []
    for g in groups.values():
        in_qty = len(g["batch_ids"])
        out_qty = len(g["batch_ids"] & delivered_set)
        stock = in_qty - out_qty

        g["report_date"] = g["min_date"].strftime("%d/%m/%Y")
        g["in_qty"] = in_qty
        g["out_qty"] = out_qty
        g["stock"] = stock
        g["total_box"] = in_qty
        g["sort_order"] = 0
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
                "min_date": rows[0]["min_date"],
                "report_date": report_date,
                "container_no": container_no,
                "item": str(len(rows)),
                "pulp": "",
                "lusture": "",
                "glue": "Total:",
                "grade": "",
                "in_qty": sum(r["in_qty"] for r in rows),
                "out_qty": sum(r["out_qty"] for r in rows),
                "stock": round(sum(r["stock"] for r in rows), 2),
                "lot_no": lot,
                "total_box": sum(r["total_box"] for r in rows),
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
                    "min_date": rows[0]["min_date"],
                    "report_date": report_date,
                    "container_no": container_no,
                    "item": str(len(rows)),
                    "pulp": "",
                    "lusture": "",
                    "glue": "Grand Total:",
                    "grade": "",
                    "in_qty": sum(r["in_qty"] for r in rows),
                    "out_qty": sum(r["out_qty"] for r in rows),
                    "stock": round(sum(r["stock"] for r in rows), 2),
                    "lot_no": "",
                    "total_box": sum(r["total_box"] for r in rows),
                    "sort_order": 2,
                }
            )

    # Step 7: Combine and sort
    all_rows = main_rows + lot_totals + container_totals
    all_rows.sort(
        key=lambda r: (
            -r["min_date"].toordinal(),
            r["container_no"],
            r["lot_no"] if r["lot_no"] else "\xff",
            r["sort_order"],
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
                "Pulp": row["pulp"],
                "Lusture": row["lusture"],
                "Glue": row["glue"],
                "Grade": row["grade"],
                "IN Qty": row["in_qty"],
                "OUT Qty": row["out_qty"],
                "Stock": row["stock"],
                "Lot Number": row["lot_no"],
                "Total Box": row["total_box"],
                "sort_order": so,
            }
        )

    return result
