# Copyright (c) 2026, reformiqo and contributors
# For license information, please see license.txt
#
# MI1-I69 (2026-06-23): converted DN from a Query Report to a Script
# Report so column labels can swap dynamically with the Transaction Type
# filter — Pulp ⇄ Type, Glue ⇄ Product, Lusture ⇄ Colour. The previous
# Query Report flavour declared columns via SQL aliases; rebinding
# labels in JS post-render didn't reliably re-draw the datatable
# headers. Script reports build their `columns` dict per-call, so the
# label swap is honoured every refresh.

import frappe
from frappe import _


def execute(filters=None):
    filters = filters or {}
    columns = get_columns(filters)
    data = get_data(filters)
    return columns, data


def get_columns(filters):
    is_hty = (filters.get("transaction_type") == "HTY")
    pulp_label = _("Type") if is_hty else _("Pulp")
    glue_label = _("Product") if is_hty else _("Glue")
    lusture_label = _("Colour") if is_hty else _("Lusture")
    return [
        {"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 110},
        {"label": _("ID"), "fieldname": "id", "fieldtype": "Data", "width": 180},
        {"label": _("Challan"), "fieldname": "challan", "fieldtype": "Data", "width": 110},
        {"label": _("Date"), "fieldname": "date", "fieldtype": "Date", "width": 100},
        {"label": _("Denier"), "fieldname": "denier", "fieldtype": "Data", "width": 110},
        {"label": pulp_label, "fieldname": "pulp", "fieldtype": "Data", "width": 90},
        {"label": glue_label, "fieldname": "glue", "fieldtype": "Data", "width": 90},
        {"label": lusture_label, "fieldname": "lusture", "fieldtype": "Data", "width": 90},
        {"label": _("Grade"), "fieldname": "grade", "fieldtype": "Data", "width": 80},
        {"label": _("Total Qty"), "fieldname": "total_qty", "fieldtype": "Float", "width": 100, "precision": 3},
        {"label": _("Merge No"), "fieldname": "merge_no", "fieldtype": "Data", "width": 90},
        {"label": _("Lot No"), "fieldname": "lot_no", "fieldtype": "Data", "width": 110},
        {"label": _("Item Length"), "fieldname": "item_length", "fieldtype": "Int", "width": 100},
        {"label": _("Container"), "fieldname": "container", "fieldtype": "Data", "width": 120},
        {"label": _("Customer Name"), "fieldname": "customer_name", "fieldtype": "Data", "width": 180},
        {"label": _("Vehicle No"), "fieldname": "vehicle_no", "fieldtype": "Data", "width": 110},
        {"label": _("Sales Person"), "fieldname": "sales_person", "fieldtype": "Data", "width": 120},
        {"label": _("Total Cone"), "fieldname": "total_cone", "fieldtype": "Float", "width": 100, "precision": 0},
        {"label": _("Supplier Batch No"), "fieldname": "supplier_batch_no", "fieldtype": "Data", "width": 200},
        {"label": _("Driver Name"), "fieldname": "driver_name", "fieldtype": "Data", "width": 140},
        {"label": _("Remark"), "fieldname": "remark", "fieldtype": "Small Text", "width": 200},
    ]


def get_data(filters):
    from_date = filters.get("from_date")
    to_date = filters.get("to_date")
    transaction_type = (filters.get("transaction_type") or "").strip()
    if not (from_date and to_date):
        return []

    tt_clause = ""
    if transaction_type in ("VFY", "HTY"):
        # Same EXISTS pattern as before: filter rows whose DN-item's
        # container_no has at least one Container doc with that
        # transaction_type. Avoids row-multiplication from many Container
        # docs sharing one container_no.
        tt_clause = """
            AND EXISTS (
                SELECT 1 FROM `tabContainer` c
                WHERE c.container_no = dni.custom_container_no
                  AND c.transaction_type = %(transaction_type)s
            )
        """

    rows = frappe.db.sql(
        f"""
        SELECT
            CASE
              WHEN dn.status = 'Completed' THEN CONCAT('<span style="color:green;">', dn.status, '</span>')
              WHEN dn.status = 'To Bill' THEN CONCAT('<span style="color:orange;">', dn.status, '</span>')
              WHEN dn.status = 'To Deliver and Bill' THEN CONCAT('<span style="color:blue;">', dn.status, '</span>')
              WHEN dn.status = 'Draft' THEN CONCAT('<span style="color:gray;">', dn.status, '</span>')
              WHEN dn.status = 'Cancelled' THEN CONCAT('<span style="color:red;">', dn.status, '</span>')
              WHEN dn.status = 'Closed' THEN CONCAT('<span style="color:purple;">', dn.status, '</span>')
              ELSE dn.status
            END AS `status`,
            CONCAT('<a href="/app/delivery-note/', dn.name, '">', dn.name, '</a>') AS `id`,
            dn.challan_number AS `challan`,
            dn.posting_date AS `date`,
            dni.item_code AS `denier`,
            SUBSTRING_INDEX(COALESCE(NULLIF(dn.custom_pulp, ''), b.custom_pulp), '-', -1) AS `pulp`,
            SUBSTRING_INDEX(COALESCE(NULLIF(dn.custom_glue, ''), b.custom_glue), '-', -1) AS `glue`,
            SUBSTRING_INDEX(COALESCE(NULLIF(dn.custom_lusture, ''), b.custom_lusture), '-', -1) AS `lusture`,
            SUBSTRING_INDEX(COALESCE(NULLIF(dn.custom_grade, ''), b.custom_grade), '-', -1) AS `grade`,
            SUM(dni.qty) AS `total_qty`,
            dn.custom_merge_no AS `merge_no`,
            dni.custom_lot_no AS `lot_no`,
            COUNT(dni.name) AS `item_length`,
            dni.custom_container_no AS `container`,
            dn.customer_name AS `customer_name`,
            dn.vehicle_no AS `vehicle_no`,
            dn.custom_sales_person AS `sales_person`,
            SUM(dni.custom_cone) AS `total_cone`,
            GROUP_CONCAT(DISTINCT dni.custom_supplier_batch_no SEPARATOR ', ') AS `supplier_batch_no`,
            dn.driver_name AS `driver_name`,
            MAX(dn.remark) AS `remark`
        FROM `tabDelivery Note` dn
        LEFT JOIN `tabDelivery Note Item` dni ON dni.parent = dn.name
        LEFT JOIN `tabBatch` b ON b.name = dni.batch_no
        WHERE
            dn.docstatus < 2
            AND dn.posting_date BETWEEN %(from_date)s AND %(to_date)s
            {tt_clause}
        GROUP BY
            dn.name,
            dni.item_code,
            dni.custom_container_no,
            dni.custom_lot_no
        ORDER BY dn.posting_date DESC, dn.name, dni.item_code
        """,
        {
            "from_date": from_date,
            "to_date": to_date,
            "transaction_type": transaction_type,
        },
        as_dict=True,
    )
    return rows
