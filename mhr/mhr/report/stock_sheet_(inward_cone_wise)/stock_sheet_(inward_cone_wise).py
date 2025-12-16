# Copyright (c) 2025, reformiqo and contributors
# For license information, please see license.txt

import frappe
from frappe import _


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
        {"label": _("Cone"), "fieldname": "Cone", "fieldtype": "Data", "width": 100},
    ]


def get_data(filters=None):
    if not filters:
        filters = {}

    # Get date filters (fdt and tdt from JSON)
    fdt = filters.get("fdt")
    tdt = filters.get("tdt")

    if not fdt or not tdt:
        frappe.throw(_("Please select From Date and To Date"))

    query = """
		WITH main_data AS (
			SELECT 
				DATE_FORMAT(b.manufacturing_date, '%%d/%%m/%%Y') AS report_date,
				b.custom_container_no AS container_no,
				b.item AS item,
				b.custom_pulp AS pulp,
				b.custom_lusture AS lusture,
				b.custom_glue AS glue,
				b.custom_grade AS grade,
				ROUND(
					SUM(b.batch_qty) -
					SUM(
						CASE 
							WHEN dn.docstatus = 1 THEN COALESCE(dni.qty, 0)
							ELSE 0
						END
					),
				2) AS in_qty,
				ROUND(
					SUM(
						CASE 
							WHEN dn.docstatus = 1 THEN COALESCE(dni.qty, 0)
							ELSE 0
						END
					),
				2) * -1 AS out_qty,
				ROUND(SUM(b.batch_qty),2) AS stock,
				b.custom_lot_no AS lot_no,
				COUNT(b.name) AS total_box,
				b.custom_cone AS cone,
				0 AS sort_order
			FROM `tabBatch` b
			LEFT JOIN `tabDelivery Note Item` dni ON b.name = dni.batch_no
			LEFT JOIN `tabDelivery Note` dn ON dn.name = dni.parent
			WHERE
				b.manufacturing_date BETWEEN %(fdt)s AND %(tdt)s
			GROUP BY
				b.manufacturing_date,
				b.custom_container_no,
				b.custom_lot_no,
				b.custom_cone,
				item,
				pulp,
				lusture,
				glue,
				grade
		),
		container_total AS (
			SELECT
				report_date,
				container_no,
				CONCAT('<b>', COUNT(item), '</b>') AS item,
				'' AS pulp,
				'' AS lusture,
				'<b>Total:</b>' AS glue,
				'' AS grade,
				SUM(in_qty) AS in_qty,
				SUM(out_qty) AS out_qty,
				ROUND(SUM(stock),2) AS stock,
				'' AS lot_no,
				SUM(total_box) AS total_box,
				'' AS cone,
				1 AS sort_order
			FROM main_data
			GROUP BY
				report_date,
				container_no
		)
		SELECT
			CASE WHEN sort_order = 1 THEN '' ELSE report_date END AS `Date`,
			CASE WHEN sort_order = 1 THEN '' ELSE container_no END AS `Container Number`,
			item AS `Item`,
			pulp AS `Pulp`,
			lusture AS `Lusture`,
			glue AS `Glue`,
			grade AS `Grade`,
			CASE
				WHEN sort_order = 0
				THEN CONCAT('<span style="color:green;">', in_qty, '</span>')
				ELSE CONCAT('<b>', in_qty, '</b>')
			END AS `IN Qty`,
			CASE
				WHEN sort_order = 0
				THEN CONCAT('<span style="color:red;">', out_qty, '</span>')
				ELSE CONCAT('<b>', out_qty, '</b>')
			END AS `OUT Qty`,
			CASE
				WHEN sort_order = 0
				THEN stock
				ELSE CONCAT('<b>', stock, '</b>')
			END AS `Stock`,
			lot_no AS `Lot Number`,
			CASE
				WHEN sort_order = 0
				THEN total_box
				ELSE CONCAT('<b>', total_box, '</b>')
			END AS `Total Box`,
			cone AS `Cone`
		FROM (
			SELECT * FROM main_data
			UNION ALL
			SELECT * FROM container_total
		) final
		ORDER BY
			STR_TO_DATE(report_date, '%%d/%%m/%%Y') DESC,
			container_no,
			sort_order,
			lot_no,
			cone
	"""

    params = {"fdt": fdt, "tdt": tdt}

    data = frappe.db.sql(query, params, as_dict=1)
    return data
