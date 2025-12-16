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
				DATE_FORMAT(b.manufacturing_date, '%%d-%%m-%%Y') AS report_date,
				b.custom_container_no AS container_no,
				b.item AS item,
				b.custom_pulp AS pulp,
				b.custom_lusture AS lusture,
				b.custom_glue AS glue,
				b.custom_grade AS grade,
				ROUND(SUM(b.batch_qty), 2) AS total_purchase_qty,
				ROUND(
					SUM(b.batch_qty) -
					SUM(
						CASE 
							WHEN dn.docstatus = 1 THEN COALESCE(dni.qty, 0)
							ELSE 0
						END
					),
				2) AS closing_stock,
				ROUND(
					SUM(
						CASE 
							WHEN dn.docstatus = 1 THEN COALESCE(dni.qty, 0)
							ELSE 0
						END
					),
				2) AS sales,
				c.merge_no AS merge_no,
				b.custom_lot_no AS lot_no,
				COUNT(DISTINCT b.custom_cone) AS no_of_cone,
				COUNT(b.name) AS stock_box,
				'' AS remark,
				0 AS sort_order
			FROM `tabBatch` b
			LEFT JOIN `tabDelivery Note Item` dni ON b.name = dni.batch_no
			LEFT JOIN `tabDelivery Note` dn ON dn.name = dni.parent
			LEFT JOIN `tabContainer` c ON c.container_no = b.custom_container_no
			WHERE
				b.manufacturing_date BETWEEN %(fdt)s AND %(tdt)s
			GROUP BY
				b.manufacturing_date,
				b.custom_container_no,
				b.custom_lot_no,
				b.item,
				b.custom_pulp,
				b.custom_lusture,
				b.custom_glue,
				b.custom_grade,
				c.merge_no
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
				ROUND(SUM(total_purchase_qty), 2) AS total_purchase_qty,
				ROUND(SUM(closing_stock), 2) AS closing_stock,
				'' AS merge_no,
				'' AS lot_no,
				SUM(no_of_cone) AS no_of_cone,
				SUM(stock_box) AS stock_box,
				ROUND(SUM(sales), 2) AS sales,
				'' AS remark,
				1 AS sort_order
			FROM main_data
			GROUP BY
				report_date,
				container_no
		)
		SELECT
			CASE WHEN sort_order = 1 THEN '' ELSE report_date END AS `Date`,
			CASE WHEN sort_order = 1 THEN '' ELSE container_no END AS `Container No`,
			item AS `Product Name`,
			pulp AS `Pulp`,
			lusture AS `Lusture`,
			glue AS `Glue`,
			grade AS `Grade`,
			CASE
				WHEN sort_order = 0
				THEN total_purchase_qty
				ELSE CONCAT('<b>', total_purchase_qty, '</b>')
			END AS `Total Op./ Purc Qty`,
			CASE
				WHEN sort_order = 0
				THEN closing_stock
				ELSE CONCAT('<b>', closing_stock, '</b>')
			END AS `Closing Stock`,
			merge_no AS `Mrg. No.`,
			lot_no AS `Lot No.`,
			CASE
				WHEN sort_order = 0
				THEN no_of_cone
				ELSE CONCAT('<b>', no_of_cone, '</b>')
			END AS `No Of Cone`,
			CASE
				WHEN sort_order = 0
				THEN stock_box
				ELSE CONCAT('<b>', stock_box, '</b>')
			END AS `Stock Box`,
			CASE
				WHEN sort_order = 0
				THEN sales
				ELSE CONCAT('<b>', sales, '</b>')
			END AS `Sales`,
			remark AS `Remark`
		FROM (
			SELECT * FROM main_data
			UNION ALL
			SELECT * FROM container_total
		) final
		ORDER BY
			STR_TO_DATE(report_date, '%%d-%%m-%%Y') DESC,
			container_no,
			sort_order,
			lot_no
	"""

    params = {"fdt": fdt, "tdt": tdt}

    data = frappe.db.sql(query, params, as_dict=1)
    return data
