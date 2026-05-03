# Copyright (c) 2026, reformiqo and contributors
# For license information, please see license.txt

import frappe
from frappe import _


def execute(filters=None):
    columns = get_columns()
    data = get_data(filters)
    return columns, data


def get_columns():
    return [
        {
            "label": _("Date"),
            "fieldname": "date",
            "fieldtype": "Date",
            "width": 100,
        },
        {
            "label": _("Challan No."),
            "fieldname": "challan_no",
            "fieldtype": "Link",
            "options": "Delivery Note",
            "width": 120,
        },
        # MI1-I28: split rows by container × lot. A DN that spans two
        # lots used to collapse into one row with combined weight;
        # now shows one row per lot.
        {
            "label": _("Container No"),
            "fieldname": "container_no",
            "fieldtype": "Data",
            "width": 130,
        },
        {
            "label": _("Lot No"),
            "fieldname": "lot_no",
            "fieldtype": "Data",
            "width": 110,
        },
        {
            "label": _("In Kgs"),
            "fieldname": "in_kgs",
            "fieldtype": "Float",
            "width": 100,
        },
        {
            "label": _("Total Kgs"),
            "fieldname": "total_kgs",
            "fieldtype": "Float",
            "width": 100,
        },
        {
            "label": _("Cust. Name"),
            "fieldname": "customer_name",
            "fieldtype": "Data",
            "width": 200,
        },
        {
            "label": _("Transporter Name"),
            "fieldname": "transporter_name",
            "fieldtype": "Data",
            "width": 180,
        },
        {
            "label": _("L.R Status"),
            "fieldname": "lr_status",
            "fieldtype": "Data",
            "width": 120,
        },
        # MI1-I24: Remark column. Sourced from Delivery Note's
        # `custom_notes` (label "Notes") since stock Delivery Note
        # has no `remarks` field on this site.
        {
            "label": _("Remark"),
            "fieldname": "remark",
            "fieldtype": "Data",
            "width": 200,
        },
    ]


def get_data(filters=None):
    if not filters:
        filters = {}

    from_date = filters.get("from_date")
    to_date = filters.get("to_date")
    transporter = filters.get("transporter")

    if not from_date or not to_date:
        frappe.throw(_("Please select From Date and To Date"))

    # Build WHERE conditions
    conditions = ["dn.posting_date BETWEEN %(from_date)s AND %(to_date)s"]
    conditions.append("dn.docstatus = 1")

    if transporter:
        conditions.append("dt.driver_name = %(transporter)s")

    where_clause = " AND ".join(conditions)

    # MI1-I24 + MI1-I28: split rows by (DN, container_no, lot_no) and
    # add a Remark column. Per-row in_kgs/total_kgs is summed from
    # Delivery Note Item rows that belong to that lot. A DN with two
    # lots now produces two rows.
    query = f"""
        SELECT
            dn.posting_date                                AS date,
            dn.name                                        AS challan_no,
            COALESCE(dni.custom_container_no, '')          AS container_no,
            COALESCE(dni.custom_lot_no, '')                AS lot_no,
            SUM(COALESCE(dni.total_weight, dni.qty * COALESCE(dni.weight_per_unit, 0))) AS in_kgs,
            SUM(COALESCE(dni.total_weight, dni.qty * COALESCE(dni.weight_per_unit, 0))) AS total_kgs,
            dn.customer_name                               AS customer_name,
            COALESCE(MAX(dt.driver_name), '')              AS transporter_name,
            COALESCE(dn.lr_no, '')                         AS lr_status,
            COALESCE(dn.custom_notes, '')                  AS remark
        FROM `tabDelivery Note` dn
        LEFT JOIN `tabDelivery Note Item` dni ON dni.parent = dn.name
        LEFT JOIN `tabDelivery Stop` ds ON ds.delivery_note = dn.name
        LEFT JOIN `tabDelivery Trip` dt ON ds.parent = dt.name AND dt.docstatus = 1
        WHERE {where_clause}
        GROUP BY dn.name, dni.custom_container_no, dni.custom_lot_no
        ORDER BY dn.posting_date ASC, dn.name ASC, lot_no ASC
    """

    params = {
        "from_date": from_date,
        "to_date": to_date,
    }

    if transporter:
        params["transporter"] = transporter

    data = frappe.db.sql(query, params, as_dict=1)
    return data
