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

import frappe
from frappe import _


def execute(filters=None):
    filters = filters or {}
    return get_columns(), get_data(filters)


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
            COALESCE(dn.custom_merge_no, '')              AS merge_no,
            COUNT(dni.name)                               AS item_length,
            dn.customer_name                              AS customer
        FROM `tabDelivery Note` dn
        LEFT JOIN `tabDelivery Note Item` dni ON dni.parent = dn.name
        WHERE {where}
        GROUP BY dn.name, dni.custom_container_no, dni.custom_lot_no
        ORDER BY dn.posting_date DESC, dn.name, dni.custom_lot_no
        """,
        params,
        as_dict=True,
    )
    return rows
