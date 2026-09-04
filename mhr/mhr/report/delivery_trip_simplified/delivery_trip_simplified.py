# Copyright (c) 2026, reformiqo and contributors
# For license information, please see license.txt
#
# MI1-I35 — Delivery Trip Simplified
#
# Reporter (Raj Tiwari) wanted a stripped-down version of the existing
# Delivery Trip report. The existing one is for Refrens; this one shows
# only the 7 fields below, in the exact order:
#
#   Departure Time | Delivery Note | Total Quantity | Customer |
#   Vehicle        | Item Length   | Driver Name
#
# Source data (no SLE / no batch math — header + first-level child only):
#   - tabDelivery Trip         (departure_time, vehicle, driver_name)
#   - tabDelivery Stop  child  (delivery_note, customer)
#   - tabDelivery Note         (total_qty, custom_item_length)
#
# One row per Delivery Stop. A Trip with N stops produces N rows.
#
# MI1-I122 (Raj 2026-09-03): Transaction Type filter (VFY / HTY, blank = both)
# and a Transaction Type column. The type is read from the transaction
# records: the stop's Delivery Note first, else the Trip header, else 'VFY'
# (legacy documents predate the field — same rule as Delivery Challan,
# MI1-I39 P2-C). The filter applies the same expression, and
# enforce_role_scoped_transaction_type forces it for single-mode users
# exactly as on every other mhr report (MI1-I61 / MI1-I80).

import frappe
from frappe import _
from frappe.utils import getdate


def execute(filters=None):
    filters = filters or {}
    from mhr.utilis import enforce_role_scoped_transaction_type
    filters = enforce_role_scoped_transaction_type(filters)
    return get_columns(), get_data(filters)


# MI1-I122: one expression for the column and the filter.
TRANSACTION_TYPE_SQL = "COALESCE(NULLIF(dn.transaction_type, ''), NULLIF(dt.transaction_type, ''), 'VFY')"


def get_columns():
    return [
        # MI1-I68: show Date only, drop the time portion.
        {"label": _("Departure Date"), "fieldname": "departure_time",
         "fieldtype": "Date", "width": 110},
        {"label": _("Delivery Note"), "fieldname": "delivery_note",
         "fieldtype": "Link", "options": "Delivery Note", "width": 160},
        {"label": _("Total Quantity"), "fieldname": "total_qty",
         "fieldtype": "Float", "width": 120, "precision": 3},
        {"label": _("Customer"), "fieldname": "customer",
         "fieldtype": "Link", "options": "Customer", "width": 180},
        {"label": _("Vehicle"), "fieldname": "vehicle",
         "fieldtype": "Link", "options": "Vehicle", "width": 130},
        {"label": _("Item Length"), "fieldname": "item_length",
         "fieldtype": "Data", "width": 110},
        {"label": _("Driver Name"), "fieldname": "driver_name",
         "fieldtype": "Data", "width": 160},
        # MI1-I122: appended so the seven MI1-I35 columns keep their order.
        {"label": _("Transaction Type"), "fieldname": "transaction_type",
         "fieldtype": "Data", "width": 120},
    ]


def get_data(filters):
    conditions = ["dt.docstatus = 1"]
    params = {}
    if filters.get("from_date"):
        conditions.append("dt.departure_time >= %(from_date)s")
        params["from_date"] = filters["from_date"]
    if filters.get("to_date"):
        # End-of-day so a "to_date" of today includes today's trips.
        conditions.append("dt.departure_time < DATE_ADD(%(to_date)s, INTERVAL 1 DAY)")
        params["to_date"] = filters["to_date"]
    if filters.get("vehicle"):
        conditions.append("dt.vehicle = %(vehicle)s")
        params["vehicle"] = filters["vehicle"]
    if filters.get("driver"):
        conditions.append("dt.driver = %(driver)s")
        params["driver"] = filters["driver"]
    if filters.get("customer"):
        conditions.append("ds.customer = %(customer)s")
        params["customer"] = filters["customer"]
    # MI1-I122: VFY / HTY narrows; blank (or anything else) shows both.
    transaction_type = (filters.get("transaction_type") or "").strip().upper()
    if transaction_type in ("VFY", "HTY"):
        conditions.append(f"{TRANSACTION_TYPE_SQL} = %(transaction_type)s")
        params["transaction_type"] = transaction_type
    where = " AND ".join(conditions)

    rows = frappe.db.sql(
        f"""
        SELECT
            dt.departure_time          AS departure_time,
            ds.delivery_note           AS delivery_note,
            COALESCE(dn.total_qty, 0)  AS total_qty,
            ds.customer                AS customer,
            dt.vehicle                 AS vehicle,
            COALESCE(dn.custom_item_length, '') AS item_length,
            dt.driver_name             AS driver_name,
            {TRANSACTION_TYPE_SQL}     AS transaction_type,
            dt.name                    AS trip,
            ds.idx                     AS stop_idx
        FROM `tabDelivery Trip` dt
        INNER JOIN `tabDelivery Stop` ds ON ds.parent = dt.name
                                         AND ds.parenttype = 'Delivery Trip'
        LEFT JOIN `tabDelivery Note`  dn ON dn.name = ds.delivery_note
        WHERE {where}
        ORDER BY dt.departure_time DESC, dt.name, ds.idx
        """,
        params,
        as_dict=True,
    )
    # Strip internal trip/stop_idx before returning — they only exist for
    # stable ordering and aren't part of the user-facing column set.
    for r in rows:
        r.pop("trip", None)
        r.pop("stop_idx", None)
    return rows
