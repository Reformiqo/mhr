# Copyright (c) 2026, reformiqo and contributors
# For license information, please see license.txt
#
# MI1-I50 P5 — Subcontractor Material Tracking
#
# Per row of a "Send to Subcontractor" Stock Entry's items table, show:
#   Send entry name + posting date + supplier (from custom field), item,
#   batch, sent qty, received qty (from the P3 hook), pending qty, and
#   the parent status (custom_subcontract_status, set by P3).
#
# Filters: date range + supplier + status. JS-side formatters colour
# pending > 0 red, fully-received rows green.

import frappe
from frappe import _
from frappe.utils import flt


PRECISION = 3


def execute(filters=None):
    filters = filters or {}
    return get_columns(), get_data(filters)


def get_columns():
    return [
        {
            "label": _("Send Entry"),
            "fieldname": "send_entry",
            "fieldtype": "Link",
            "options": "Stock Entry",
            "width": 160,
        },
        {
            "label": _("Date"),
            "fieldname": "posting_date",
            "fieldtype": "Date",
            "width": 100,
        },
        {
            "label": _("Supplier"),
            "fieldname": "supplier",
            "fieldtype": "Link",
            "options": "Supplier",
            "width": 180,
        },
        {
            "label": _("Item"),
            "fieldname": "item_code",
            "fieldtype": "Link",
            "options": "Item",
            "width": 140,
        },
        {
            "label": _("Batch"),
            "fieldname": "batch_no",
            "fieldtype": "Link",
            "options": "Batch",
            "width": 130,
        },
        {
            "label": _("Sent Qty"),
            "fieldname": "sent_qty",
            "fieldtype": "Float",
            "width": 100,
            "precision": PRECISION,
        },
        {
            "label": _("Received Qty"),
            "fieldname": "received_qty",
            "fieldtype": "Float",
            "width": 110,
            "precision": PRECISION,
        },
        {
            "label": _("Pending Qty"),
            "fieldname": "pending_qty",
            "fieldtype": "Float",
            "width": 110,
            "precision": PRECISION,
        },
        {
            "label": _("Status"),
            "fieldname": "status",
            "fieldtype": "Data",
            "width": 130,
        },
    ]


def get_data(filters):
    """Pull rows of every Send-to-Subcontractor Stock Entry's items table,
    annotated with the recompute fields written by P3."""
    conditions = ["se.docstatus = 1", "se.purpose = 'Send to Subcontractor'"]
    params = {}

    if filters.get("from_date"):
        conditions.append("se.posting_date >= %(from_date)s")
        params["from_date"] = filters["from_date"]
    if filters.get("to_date"):
        conditions.append("se.posting_date <= %(to_date)s")
        params["to_date"] = filters["to_date"]
    if filters.get("supplier"):
        conditions.append("se.supplier = %(supplier)s")
        params["supplier"] = filters["supplier"]
    if filters.get("status"):
        # custom_subcontract_status NULL on legacy / never-touched rows ->
        # treat as Open for the filter.
        if filters["status"] == "Open":
            conditions.append(
                "(se.custom_subcontract_status IS NULL OR "
                "se.custom_subcontract_status = '' OR "
                "se.custom_subcontract_status = 'Open')"
            )
        else:
            conditions.append("se.custom_subcontract_status = %(status)s")
            params["status"] = filters["status"]

    where_sql = " AND ".join(conditions)

    rows = frappe.db.sql(
        f"""
        SELECT
            se.name                                    AS send_entry,
            se.posting_date                            AS posting_date,
            se.supplier                                AS supplier,
            COALESCE(se.custom_subcontract_status, 'Open') AS status,
            sed.item_code                              AS item_code,
            sed.batch_no                               AS batch_no,
            sed.qty                                    AS sent_qty,
            COALESCE(sed.custom_received_qty, 0)       AS received_qty,
            COALESCE(sed.custom_pending_qty,
                     sed.qty - COALESCE(sed.custom_received_qty, 0)) AS pending_qty
        FROM `tabStock Entry` se
        INNER JOIN `tabStock Entry Detail` sed
            ON sed.parent = se.name AND sed.parenttype = 'Stock Entry'
        WHERE {where_sql}
        ORDER BY se.posting_date DESC, se.name DESC, sed.idx ASC
        """,
        params,
        as_dict=True,
    )

    # Round + recompute pending defensively (legacy entries can have NULL
    # custom_pending_qty if P3 never fired for them).
    for r in rows:
        r["sent_qty"] = flt(r["sent_qty"], PRECISION)
        r["received_qty"] = flt(r["received_qty"], PRECISION)
        if r["pending_qty"] is None:
            r["pending_qty"] = r["sent_qty"] - r["received_qty"]
        r["pending_qty"] = flt(r["pending_qty"], PRECISION)

    return rows
