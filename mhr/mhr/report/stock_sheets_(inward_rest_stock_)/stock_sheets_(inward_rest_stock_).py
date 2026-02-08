# Copyright (c) 2025, reformiqo and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import flt
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
            "label": _("Container No"),
            "fieldname": "Container No",
            "fieldtype": "Data",
            "width": 150,
        },
        {
            "label": _("Product Name"),
            "fieldname": "Product Name",
            "fieldtype": "Data",
            "width": 150,
        },
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
            "label": _("Total Op./ Purc Qty"),
            "fieldname": "Total Op./ Purc Qty",
            "fieldtype": "Float",
            "width": 120,
        },
        {
            "label": _("Closing Stock"),
            "fieldname": "Closing Stock",
            "fieldtype": "Float",
            "width": 120,
        },
        {
            "label": _("Mrg. No."),
            "fieldname": "Mrg. No.",
            "fieldtype": "Data",
            "width": 100,
        },
        {
            "label": _("Lot No."),
            "fieldname": "Lot No.",
            "fieldtype": "Data",
            "width": 120,
        },
        {
            "label": _("No Of Cone"),
            "fieldname": "No Of Cone",
            "fieldtype": "Float",
            "width": 100,
        },
        {
            "label": _("Stock Box"),
            "fieldname": "Stock Box",
            "fieldtype": "Int",
            "width": 100,
        },
        {"label": _("Sales"), "fieldname": "Sales", "fieldtype": "Float", "width": 100},
        {
            "label": _("Remark"),
            "fieldname": "Remark",
            "fieldtype": "Data",
            "width": 150,
        },
        {
            "label": _("sort_order"),
            "fieldname": "sort_order",
            "fieldtype": "Int",
            "width": 0,
            "hidden": 1,
        },
    ]


def get_delivered_quantities(batch_ids):
    """Get total delivered qty per batch from Delivery Note Items, in chunks."""
    if not batch_ids:
        return {}

    delivered_map = {}
    CHUNK = 2000

    DNI = frappe.qb.DocType("Delivery Note Item")
    DN = frappe.qb.DocType("Delivery Note")

    for i in range(0, len(batch_ids), CHUNK):
        chunk = batch_ids[i : i + CHUNK]

        rows = (
            frappe.qb.from_(DNI)
            .inner_join(DN)
            .on(DN.name == DNI.parent)
            .select(DNI.batch_no, Sum(DNI.qty).as_("total_delivered"))
            .where(DN.docstatus == 1)
            .where(DNI.batch_no.isin(chunk))
            .groupby(DNI.batch_no)
        ).run(as_dict=True)

        for r in rows:
            delivered_map[r.batch_no] = delivered_map.get(r.batch_no, 0) + flt(
                r.total_delivered
            )

    return delivered_map


def get_merge_numbers(container_nos):
    """Get merge_no for containers."""
    if not container_nos:
        return {}

    Container = frappe.qb.DocType("Container")
    rows = (
        frappe.qb.from_(Container)
        .select(Container.container_no, Container.merge_no)
        .where(Container.container_no.isin(list(container_nos)))
    ).run(as_dict=True)

    return {r.container_no: r.merge_no or "" for r in rows}


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
            Batch.manufacturing_date,
            Batch.batch_qty,
        )
        .where(Batch.manufacturing_date >= fdt)
        .where(Batch.manufacturing_date <= tdt)
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

    # Step 2: Get delivered quantities per batch
    delivered_map = get_delivered_quantities(batch_ids)

    # Step 3: Get merge_no from Container table
    container_nos = set(b.container_no for b in batches if b.container_no)
    merge_map = get_merge_numbers(container_nos)

    # Step 4: Aggregate by group key in Python
    groups = {}
    for b in batches:
        batch_date = b.manufacturing_date
        merge_no = merge_map.get(b.container_no or "", "")
        key = (
            batch_date,
            b.container_no or "",
            b.lot_no or "",
            b.item or "",
            b.pulp or "",
            b.lusture or "",
            b.glue or "",
            b.grade or "",
            merge_no,
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
                "merge_no": merge_no,
                "total_purchase_qty": 0.0,
                "total_delivered": 0.0,
                "cones": set(),
                "batch_count": 0,
            }

        groups[key]["total_purchase_qty"] += flt(b.batch_qty)
        groups[key]["total_delivered"] += flt(delivered_map.get(b.batch_id, 0))
        if b.cone:
            groups[key]["cones"].add(b.cone)
        groups[key]["batch_count"] += 1

    # Step 5: Build main data rows (only where closing_stock > 0)
    main_rows = []
    for g in groups.values():
        closing_stock = round(g["total_purchase_qty"] - g["total_delivered"], 2)
        if closing_stock <= 0:
            continue

        report_date = g["batch_date"].strftime("%d-%m-%Y") if g["batch_date"] else ""
        main_rows.append(
            {
                "batch_date": g["batch_date"],
                "report_date": report_date,
                "container_no": g["container_no"],
                "item": g["item"],
                "pulp": g["pulp"],
                "lusture": g["lusture"],
                "glue": g["glue"],
                "grade": g["grade"],
                "total_purchase_qty": round(g["total_purchase_qty"], 2),
                "closing_stock": closing_stock,
                "sales": round(g["total_delivered"], 2),
                "merge_no": g["merge_no"],
                "lot_no": g["lot_no"],
                "no_of_cone": len(g["cones"]),
                "stock_box": g["batch_count"],
                "remark": "",
                "sort_order": 0,
            }
        )

    if not main_rows:
        return []

    # Step 6: Compute container totals (sort_order=1)
    container_groups = defaultdict(list)
    for row in main_rows:
        ck = (row["report_date"], row["container_no"])
        container_groups[ck].append(row)

    container_totals = []
    for (report_date, container_no), rows in container_groups.items():
        container_totals.append(
            {
                "batch_date": rows[0]["batch_date"],
                "report_date": report_date,
                "container_no": container_no,
                "item": str(len(rows)),
                "pulp": "",
                "lusture": "",
                "glue": "Total:",
                "grade": "",
                "total_purchase_qty": round(
                    sum(r["total_purchase_qty"] for r in rows), 2
                ),
                "closing_stock": round(sum(r["closing_stock"] for r in rows), 2),
                "sales": round(sum(r["sales"] for r in rows), 2),
                "merge_no": "",
                "lot_no": "",
                "no_of_cone": sum(r["no_of_cone"] for r in rows),
                "stock_box": sum(r["stock_box"] for r in rows),
                "remark": "",
                "sort_order": 1,
            }
        )

    # Step 7: Combine and sort
    all_rows = main_rows + container_totals
    all_rows.sort(
        key=lambda r: (
            -(r["batch_date"].toordinal() if r["batch_date"] else 0),
            r["container_no"],
            r["sort_order"],
            r["lot_no"],
        )
    )

    # Step 8: Format output
    result = []
    for row in all_rows:
        so = row["sort_order"]
        result.append(
            {
                "Date": "" if so == 1 else row["report_date"],
                "Container No": "" if so == 1 else row["container_no"],
                "Product Name": row["item"],
                "Pulp": row["pulp"],
                "Lusture": row["lusture"],
                "Glue": row["glue"],
                "Grade": row["grade"],
                "Total Op./ Purc Qty": row["total_purchase_qty"],
                "Closing Stock": row["closing_stock"],
                "Mrg. No.": row["merge_no"],
                "Lot No.": row["lot_no"],
                "No Of Cone": row["no_of_cone"],
                "Stock Box": row["stock_box"],
                "Sales": row["sales"],
                "Remark": row["remark"],
                "sort_order": so,
            }
        )

    return result
