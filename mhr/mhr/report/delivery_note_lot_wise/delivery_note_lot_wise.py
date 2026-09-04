# Copyright (c) 2026, reformiqo and contributors
# For license information, please see license.txt
#
# MI1-I28 (reopen) — Delivery Note Lot-Wise
#
# Raj's reopen comment shows the existing "DN" list view collapses a
# multi-lot Delivery Note into one row with combined qty and the lots
# concatenated as text in Lot No (e.g. "01022026, 02022026"). He wants
# one row per lot.
#
# The fix I shipped for MI1-I28 (commit 380dfe4) was on the "Delivery
# Challan" report — and it does split correctly. But Raj's day-to-day
# view is "DN" which is the standard Delivery Note list (not a report),
# so my fix didn't reach the screen he uses.
#
# This new Script Report ("Delivery Note Lot-Wise") gives him the
# screen he wants. One row per (DN, container_no, lot_no), with all
# the columns from his screenshot.
#
# Source:  tabDelivery Note ⋈ tabDelivery Note Item
# Group:   dn.name × dni.custom_container_no × dni.custom_lot_no
#
# MI1-I116 (Raj 2026-08-31): Merge No comes from the Container master per
# (container, lot) — see mhr.mhr.report.dn.dn._merge_numbers_by_container_and_lot
# — not from the note header, which carries one aggregated value for the
# whole note and therefore showed the first container's Merge No on every lot.

import frappe
from frappe import _


def execute(filters=None):
    filters = filters or {}
    return get_columns(), get_data(filters)


def _so_progress_columns():
    """MI1-I120: Sales Order / SO Total / SO Delivered / SO Remaining.
    Lazy import — mhr.utilis is imported lazily throughout the reports."""
    from mhr.utilis import SO_PROGRESS_COLUMNS
    return [dict(c) for c in SO_PROGRESS_COLUMNS]


def get_columns():
    return [
        {"label": _("Status"),       "fieldname": "status",        "fieldtype": "Data",  "width": 110},
        {"label": _("ID"),           "fieldname": "name",          "fieldtype": "Link",  "options": "Delivery Note", "width": 170},
        {"label": _("Challan"),      "fieldname": "challan",       "fieldtype": "Data",  "width": 110},
        {"label": _("Date"),         "fieldname": "posting_date",  "fieldtype": "Date",  "width": 100},
        {"label": _("Denier"),       "fieldname": "denier",        "fieldtype": "Data",  "width": 90},
        {"label": _("Pulp"),         "fieldname": "pulp",          "fieldtype": "Data",  "width": 90},
        {"label": _("Glue"),         "fieldname": "glue",          "fieldtype": "Data",  "width": 90},
        {"label": _("Lusture"),      "fieldname": "lusture",       "fieldtype": "Data",  "width": 90},
        {"label": _("Grade"),        "fieldname": "grade",         "fieldtype": "Data",  "width": 90},
        {"label": _("Container No"), "fieldname": "container_no",  "fieldtype": "Data",  "width": 130},
        {"label": _("Lot No"),       "fieldname": "lot_no",        "fieldtype": "Data",  "width": 110},
        {"label": _("Total Qty"),    "fieldname": "total_qty",     "fieldtype": "Float", "width": 100, "precision": 3},
        {"label": _("Merge No"),     "fieldname": "merge_no",      "fieldtype": "Data",  "width": 100},
        {"label": _("Item Length"),  "fieldname": "item_length",   "fieldtype": "Int",   "width": 90},
        {"label": _("Customer"),     "fieldname": "customer",      "fieldtype": "Link",  "options": "Customer", "width": 180},
        # MI1-I120 (Raj 2026-09-02): SO No / SO Total / SO Delivered / SO Remaining.
        *_so_progress_columns(),
    ]


def get_data(filters):
    conditions = ["dn.docstatus = 1"]
    params = {}
    if filters.get("from_date"):
        conditions.append("dn.posting_date >= %(from_date)s")
        params["from_date"] = filters["from_date"]
    if filters.get("to_date"):
        conditions.append("dn.posting_date <= %(to_date)s")
        params["to_date"] = filters["to_date"]
    if filters.get("customer"):
        conditions.append("dn.customer = %(customer)s")
        params["customer"] = filters["customer"]
    if filters.get("delivery_note"):
        conditions.append("dn.name = %(delivery_note)s")
        params["delivery_note"] = filters["delivery_note"]
    if filters.get("container_no"):
        conditions.append("dni.custom_container_no = %(container_no)s")
        params["container_no"] = filters["container_no"]
    if filters.get("lot_no"):
        conditions.append("dni.custom_lot_no = %(lot_no)s")
        params["lot_no"] = filters["lot_no"]
    where = " AND ".join(conditions)

    rows = frappe.db.sql(
        f"""
        SELECT
            dn.status                                     AS status,
            dn.name                                       AS name,
            COALESCE(dn.lr_no, '')                        AS challan,
            dn.posting_date                               AS posting_date,
            COALESCE(dn.custom_denier, '')                AS denier,
            COALESCE(dn.custom_pulp, '')                  AS pulp,
            COALESCE(dn.custom_glue, '')                  AS glue,
            COALESCE(dn.custom_lusture, '')               AS lusture,
            COALESCE(dn.custom_grade, '')                 AS grade,
            COALESCE(dni.custom_container_no, '')         AS container_no,
            COALESCE(dni.custom_lot_no, '')               AS lot_no,
            SUM(COALESCE(dni.qty, 0))                     AS total_qty,
            -- MI1-I116: Merge No is NOT the note header's field (a note-level
            -- aggregate that showed the first container's value on every
            -- row). Filled below per (container, lot) from the Container
            -- master, the same resolver the DN report uses.
            ''                                            AS merge_no,
            COUNT(dni.name)                               AS item_length,
            dn.customer_name                              AS customer,
            -- MI1-I120: the order this note delivers against — the header
            -- link, else the per-row link the SO -> DN mapper writes.
            COALESCE(NULLIF(dn.custom_sales_order, ''), MAX(dni.against_sales_order)) AS sales_order
        FROM `tabDelivery Note` dn
        LEFT JOIN `tabDelivery Note Item` dni ON dni.parent = dn.name
        WHERE {where}
        GROUP BY dn.name, dni.custom_container_no, dni.custom_lot_no
        ORDER BY dn.posting_date DESC, dn.name, dni.custom_lot_no
        """,
        params,
        as_dict=True,
    )
    # MI1-I116 (Raj 2026-08-31): Merge No per (container, lot) from the
    # Container master — never the parent note's field or the first
    # container's value.
    from mhr.mhr.report.dn.dn import _container_lot_key, _merge_numbers_by_container_and_lot
    merge_numbers = _merge_numbers_by_container_and_lot(rows)
    for row in rows:
        row["merge_no"] = merge_numbers.get(_container_lot_key(row)) or ""

    # MI1-I120: SO Total / Delivered / Remaining per Sales Order; rows
    # without one stay blank.
    from mhr.utilis import annotate_so_progress
    return annotate_so_progress(rows)
