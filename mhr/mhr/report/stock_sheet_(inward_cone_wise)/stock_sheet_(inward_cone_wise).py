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
    container = filters.get("container")
    lot_no = filters.get("lot_no")
    cone = filters.get("cone")

    if not fdt or not tdt:
        frappe.throw(_("Please select From Date and To Date"))

    # Build WHERE conditions dynamically
    where_conditions = ["b.manufacturing_date BETWEEN %(fdt)s AND %(tdt)s"]

    if container:
        where_conditions.append("b.custom_container_no = %(container)s")

    if lot_no:
        where_conditions.append("b.custom_lot_no = %(lot_no)s")

    if cone:
        where_conditions.append("b.custom_cone = %(cone)s")

    where_clause = " AND ".join(where_conditions)

    query = f"""
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
					COALESCE(SUM(
						CASE
							WHEN pr.docstatus = 1 THEN pri.qty
							ELSE 0
						END
					), 0),
				2) AS in_qty,
				ROUND(
					COALESCE(SUM(
						CASE
							WHEN dn.docstatus = 1 THEN dni.qty
							ELSE 0
						END
					), 0),
				2) AS out_qty,
				ROUND(
					COALESCE(SUM(
						CASE
							WHEN pr.docstatus = 1 THEN pri.qty
							ELSE 0
						END
					), 0) -
					COALESCE(SUM(
						CASE
							WHEN dn.docstatus = 1 THEN dni.qty
							ELSE 0
						END
					), 0),
				2) AS stock,
				b.custom_lot_no AS lot_no,
				COUNT(DISTINCT b.name) AS total_box,
				b.custom_cone AS cone,
				0 AS sort_order
			FROM `tabBatch` b
			LEFT JOIN `tabPurchase Receipt Item` pri ON b.name = pri.batch_no
			LEFT JOIN `tabPurchase Receipt` pr ON pr.name = pri.parent
			LEFT JOIN `tabDelivery Note Item` dni ON b.name = dni.batch_no
			LEFT JOIN `tabDelivery Note` dn ON dn.name = dni.parent
			WHERE
				{where_clause}
				AND NOT EXISTS (
					SELECT 1 FROM `tabContainer` c
					WHERE c.container_no = b.custom_container_no
					AND c.docstatus = 2
				)
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
		lot_total AS (
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
				lot_no,
				SUM(total_box) AS total_box,
				'' AS cone,
				1 AS sort_order
			FROM main_data
			GROUP BY
				report_date,
				container_no,
				lot_no
		),
		container_lot_count AS (
			SELECT
				report_date,
				container_no,
				COUNT(DISTINCT lot_no) AS lot_count
			FROM main_data
			GROUP BY
				report_date,
				container_no
		),
		container_total AS (
			SELECT
				m.report_date,
				m.container_no,
				CONCAT('<b>', COUNT(m.item), '</b>') AS item,
				'' AS pulp,
				'' AS lusture,
				'<b>Grand Total:</b>' AS glue,
				'' AS grade,
				SUM(m.in_qty) AS in_qty,
				SUM(m.out_qty) AS out_qty,
				ROUND(SUM(m.stock),2) AS stock,
				'' AS lot_no,
				SUM(m.total_box) AS total_box,
				'' AS cone,
				2 AS sort_order
			FROM main_data m
			INNER JOIN container_lot_count c
				ON m.report_date = c.report_date
				AND m.container_no = c.container_no
			WHERE c.lot_count > 1
			GROUP BY
				m.report_date,
				m.container_no
		)
		SELECT
			CASE WHEN sort_order >= 1 THEN '' ELSE report_date END AS `Date`,
			CASE WHEN sort_order >= 1 THEN '' ELSE container_no END AS `Container Number`,
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
			SELECT * FROM lot_total
			UNION ALL
			SELECT * FROM container_total
		) final
		ORDER BY
			STR_TO_DATE(report_date, '%%d/%%m/%%Y') DESC,
			container_no,
			CASE WHEN lot_no = '' THEN 'ZZZZZ' ELSE lot_no END,
			sort_order,
			cone
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
