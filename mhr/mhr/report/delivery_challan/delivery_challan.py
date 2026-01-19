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

    query = f"""
        SELECT
            dn.posting_date AS date,
            dn.name AS challan_no,
            dn.total_net_weight AS in_kgs,
            dn.total_net_weight AS total_kgs,
            dn.customer_name AS customer_name,
            COALESCE(dt.driver_name, '') AS transporter_name,
            COALESCE(dn.lr_no, '') AS lr_status
        FROM `tabDelivery Note` dn
        LEFT JOIN `tabDelivery Stop` ds ON ds.delivery_note = dn.name
        LEFT JOIN `tabDelivery Trip` dt ON ds.parent = dt.name AND dt.docstatus = 1
        WHERE {where_clause}
        GROUP BY dn.name
        ORDER BY dn.posting_date ASC, dn.name ASC
    """

    params = {
        "from_date": from_date,
        "to_date": to_date,
    }

    if transporter:
        params["transporter"] = transporter

    data = frappe.db.sql(query, params, as_dict=1)
    return data
