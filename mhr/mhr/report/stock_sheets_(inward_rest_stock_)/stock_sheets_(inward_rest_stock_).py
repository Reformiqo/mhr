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
    container = filters.get("container")
    lot_no = filters.get("lot_no")
    cone = filters.get("cone")

    if not fdt or not tdt:
        frappe.throw(_("Please select From Date and To Date"))

    # Build WHERE conditions dynamically for optimization
    where_conditions = ["b.manufacturing_date BETWEEN %(fdt)s AND %(tdt)s"]

    if container:
        where_conditions.append("b.custom_container_no = %(container)s")

    if lot_no:
        where_conditions.append("b.custom_lot_no = %(lot_no)s")

    if cone:
        where_conditions.append("b.custom_cone = %(cone)s")

    where_clause = " AND ".join(where_conditions)

    query = f"""
		WITH batch_filtered AS (
			SELECT
				b.name,
				DATE_FORMAT(b.manufacturing_date, '%%d-%%m-%%Y') AS report_date,
				b.custom_container_no AS container_no,
				b.item AS item,
				b.custom_pulp AS pulp,
				b.custom_lusture AS lusture,
				b.custom_glue AS glue,
				b.custom_grade AS grade,
				b.batch_qty,
				b.custom_lot_no AS lot_no,
				b.custom_cone AS cone,
				b.manufacturing_date
			FROM `tabBatch` b
			WHERE
				{where_clause}
		),
		delivery_agg AS (
			SELECT
				dni.batch_no,
				SUM(CASE WHEN dn.docstatus = 1 THEN dni.qty ELSE 0 END) AS total_delivered
			FROM `tabDelivery Note Item` dni
			INNER JOIN `tabDelivery Note` dn ON dn.name = dni.parent
			WHERE dni.batch_no IN (SELECT name FROM batch_filtered)
			GROUP BY dni.batch_no
		),
		main_data AS (
			SELECT
				bf.report_date,
				bf.container_no,
				bf.item,
				bf.pulp,
				bf.lusture,
				bf.glue,
				bf.grade,
				ROUND(SUM(bf.batch_qty), 2) AS total_purchase_qty,
				ROUND(SUM(bf.batch_qty) - COALESCE(SUM(da.total_delivered), 0), 2) AS closing_stock,
				ROUND(COALESCE(SUM(da.total_delivered), 0), 2) AS sales,
				c.merge_no AS merge_no,
				bf.lot_no,
				COUNT(DISTINCT bf.cone) AS no_of_cone,
				COUNT(bf.name) AS stock_box,
				'' AS remark,
				0 AS sort_order
			FROM batch_filtered bf
			LEFT JOIN delivery_agg da ON bf.name = da.batch_no
			LEFT JOIN `tabContainer` c ON c.container_no = bf.container_no
			GROUP BY
				bf.report_date,
				bf.container_no,
				bf.lot_no,
				bf.item,
				bf.pulp,
				bf.lusture,
				bf.glue,
				bf.grade,
				c.merge_no
			HAVING ROUND(SUM(bf.batch_qty) - COALESCE(SUM(da.total_delivered), 0), 2) > 0
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

    if container:
        params["container"] = container

    if lot_no:
        params["lot_no"] = lot_no

    if cone:
        params["cone"] = cone

    data = frappe.db.sql(query, params, as_dict=1)
    return data
