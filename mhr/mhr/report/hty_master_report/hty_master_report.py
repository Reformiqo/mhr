# Copyright (c) 2026, reformiqo and contributors
# For license information, please see license.txt
#
# MI1-I39 — Phase 2D — HTY Master Report
# Per FRD §REPORT 6: HTY MASTER REPORT.
#
# Columns: Date | Container No | Item | Type | Colour | Product | Grade |
#          Lot No | IN Qty | OUT Qty | Closing Stock
#
# Filters: Date From + Date To (mandatory), Item, Colour, Grade, Product,
#          Type, Company.
#
# Architecture follows the mhr stock-report convention (CLAUDE.md):
#   1) Query Containers with frappe.qb (small N — header filter).
#   2) Collect their Batch master names from `tabBatch Items` (medium N).
#   3) Query SLE in 2000-batch chunks, aggregate in Python dicts
#      (cheap memory, predictable SQL plan).
#
# Note on "Closing Stock": defined as SUM(actual_qty) per Container across
# every batch in that Container, including movements OUTSIDE the date range
# (otherwise the closing balance is wrong). IN/OUT Qty are scoped to the
# date range — that's what the FRD shows in the month-end view.

import frappe
from frappe import _
from frappe.utils import flt, getdate

SLE_CHUNK = 2000


def execute(filters=None):
    filters = frappe._dict(filters or {})
    _validate_filters(filters)
    columns = get_columns()
    data = get_data(filters)
    return columns, data


def _validate_filters(filters):
    if not filters.get("from_date") or not filters.get("to_date"):
        frappe.throw(_("Both 'Date From' and 'Date To' are required."))
    if getdate(filters.from_date) > getdate(filters.to_date):
        frappe.throw(_("'Date From' must be on or before 'Date To'."))


def get_columns():
    return [
        {"label": _("Date"),          "fieldname": "date",          "fieldtype": "Date",   "width": 100},
        {"label": _("Container No"),  "fieldname": "container_no",  "fieldtype": "Link",   "options": "Container", "width": 150},
        {"label": _("Item"),          "fieldname": "item",          "fieldtype": "Link",   "options": "Item",      "width": 120},
        {"label": _("Type"),          "fieldname": "type",          "fieldtype": "Data",   "width": 100},
        {"label": _("Colour"),        "fieldname": "colour",        "fieldtype": "Data",   "width": 100},
        {"label": _("Product"),       "fieldname": "product",       "fieldtype": "Data",   "width": 100},
        {"label": _("Grade"),         "fieldname": "grade",         "fieldtype": "Data",   "width": 100},
        {"label": _("Lot No"),        "fieldname": "lot_no",        "fieldtype": "Data",   "width": 110},
        {"label": _("IN Qty"),        "fieldname": "in_qty",        "fieldtype": "Float",  "width": 100, "precision": 3},
        {"label": _("OUT Qty"),       "fieldname": "out_qty",       "fieldtype": "Float",  "width": 100, "precision": 3},
        {"label": _("Closing Stock"), "fieldname": "closing_stock", "fieldtype": "Float",  "width": 120, "precision": 3},
    ]


def get_data(filters):
    containers = _fetch_containers(filters)
    if not containers:
        return []

    # Map: container.name -> list of batch ids from its Batch Items child.
    container_batches = _fetch_container_batches([c.name for c in containers])
    all_batch_ids = sorted({bid for ids in container_batches.values() for bid in ids})

    if not all_batch_ids:
        # Containers with no batches still appear as zero rows — keep them visible.
        return [_row_from_container(c, in_qty=0, out_qty=0, closing=0) for c in containers]

    # Aggregate SLE per batch.
    in_per_batch, out_per_batch, closing_per_batch = _aggregate_sle(
        all_batch_ids, filters.from_date, filters.to_date
    )

    # Roll up batch totals to container.
    rows = []
    for c in containers:
        batch_ids = container_batches.get(c.name, [])
        in_qty = sum(in_per_batch.get(b, 0.0) for b in batch_ids)
        out_qty = sum(out_per_batch.get(b, 0.0) for b in batch_ids)
        closing = sum(closing_per_batch.get(b, 0.0) for b in batch_ids)
        rows.append(_row_from_container(c, in_qty, out_qty, closing))

    return rows


def _row_from_container(c, in_qty, out_qty, closing):
    return {
        "date": c.posting_date,
        "container_no": c.name,
        "item": c.item,
        # HTY semantic mapping: lusture→Colour, glue→Product, pulp→Type.
        # The column label "Type/Colour/Product" reflects HTY mode; the raw
        # field name remains the Meher one in DB.
        "type":   c.pulp,
        "colour": c.lusture,
        "product": c.glue,
        "grade":  c.grade,
        "lot_no": c.lot_no,
        "in_qty":  flt(in_qty, 3),
        "out_qty": flt(out_qty, 3),
        "closing_stock": flt(closing, 3),
    }


def _fetch_containers(filters):
    conditions = ["c.docstatus = 1", "c.posting_date BETWEEN %(from_date)s AND %(to_date)s"]
    params = {"from_date": filters.from_date, "to_date": filters.to_date}
    for fld in ("item", "company"):
        if filters.get(fld):
            conditions.append(f"c.{fld} = %({fld})s")
            params[fld] = filters[fld]
    # HTY column-name aliases map to Meher column names in DB.
    field_alias = {
        "colour":  "lusture",
        "product": "glue",
        "type":    "pulp",
        "grade":   "grade",
    }
    for alias, real in field_alias.items():
        if filters.get(alias):
            conditions.append(f"c.{real} = %({alias})s")
            params[alias] = filters[alias]
    where = " AND ".join(conditions)
    return frappe.db.sql(
        f"""
        SELECT c.name, c.posting_date, c.item, c.lusture, c.glue, c.pulp,
               c.grade, c.lot_no, c.company
        FROM `tabContainer` c
        WHERE {where}
        ORDER BY c.posting_date, c.name
        """,
        params,
        as_dict=True,
    )


def _fetch_container_batches(container_names):
    if not container_names:
        return {}
    placeholders = ", ".join(["%s"] * len(container_names))
    rows = frappe.db.sql(
        f"""
        SELECT parent, batch_id
        FROM `tabBatch Items`
        WHERE parent IN ({placeholders})
          AND parenttype = 'Container'
          AND IFNULL(batch_id, '') != ''
        """,
        tuple(container_names),
        as_dict=True,
    )
    out = {}
    for r in rows:
        out.setdefault(r.parent, []).append(r.batch_id)
    return out


def _aggregate_sle(batch_ids, from_date, to_date):
    """Read SLE in fixed chunks; aggregate IN/OUT (date-bound) and a global
    closing balance (no date upper-bound is applied to closing_per_batch — by
    design, closing is the all-time balance as of report end-date, since
    cones can be received before from_date and dispatched within range)."""
    in_per = {}
    out_per = {}
    closing_per = {}
    for i in range(0, len(batch_ids), SLE_CHUNK):
        chunk = batch_ids[i : i + SLE_CHUNK]
        placeholders = ", ".join(["%s"] * len(chunk))
        rows = frappe.db.sql(
            f"""
            SELECT batch_no, posting_date, actual_qty, is_cancelled
            FROM `tabStock Ledger Entry`
            WHERE batch_no IN ({placeholders})
              AND IFNULL(is_cancelled, 0) = 0
              AND posting_date <= %s
            """,
            (*chunk, to_date),
            as_dict=True,
        )
        for r in rows:
            qty = flt(r.actual_qty)
            closing_per[r.batch_no] = closing_per.get(r.batch_no, 0.0) + qty
            # IN/OUT only for movements WITHIN [from_date, to_date].
            if getdate(r.posting_date) >= getdate(from_date):
                if qty > 0:
                    in_per[r.batch_no] = in_per.get(r.batch_no, 0.0) + qty
                elif qty < 0:
                    out_per[r.batch_no] = out_per.get(r.batch_no, 0.0) + abs(qty)
    return in_per, out_per, closing_per
