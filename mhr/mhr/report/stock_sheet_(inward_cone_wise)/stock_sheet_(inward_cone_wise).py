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
    where_conditions = ["c.posting_date BETWEEN %(fdt)s AND %(tdt)s", "c.docstatus != 2"]

    if container:
        where_conditions.append("c.container_no = %(container)s")

    if lot_no:
        where_conditions.append("c.lot_no = %(lot_no)s")

    if cone:
        where_conditions.append("bi.cone = %(cone)s")

    where_clause = " AND ".join(where_conditions)

    query = f"""
		WITH main_data AS (
			SELECT
				DATE_FORMAT(c.posting_date, '%%d/%%m/%%Y') AS report_date,
				c.container_no AS container_no,
				bi.item AS item,
				c.pulp AS pulp,
				c.lusture AS lusture,
				c.glue AS glue,
				c.grade AS grade,
				ROUND(
					SUM(CAST(bi.qty AS DECIMAL(18,2))) +
					COALESCE((
						SELECT SUM(dni2.qty)
						FROM `tabDelivery Note Item` dni2
						INNER JOIN `tabDelivery Note` dn2 ON dn2.name = dni2.parent
						WHERE dni2.batch_no = bi.batch_id
						AND dn2.docstatus = 1
						AND dn2.is_return = 1
					), 0),
				2) AS in_qty,
				ROUND(
					COALESCE((
						SELECT SUM(dni3.qty)
						FROM `tabDelivery Note Item` dni3
						INNER JOIN `tabDelivery Note` dn3 ON dn3.name = dni3.parent
						WHERE dni3.batch_no = bi.batch_id
						AND dn3.docstatus = 1
						AND COALESCE(dn3.is_return, 0) = 0
					), 0) +
					COALESCE((
						SELECT SUM(pri2.qty)
						FROM `tabPurchase Receipt Item` pri2
						INNER JOIN `tabPurchase Receipt` pr2 ON pr2.name = pri2.parent
						WHERE pri2.batch_no = bi.batch_id
						AND pr2.docstatus = 1
						AND pr2.is_return = 1
					), 0),
				2) AS out_qty,
				ROUND(
					(SUM(CAST(bi.qty AS DECIMAL(18,2))) +
					COALESCE((
						SELECT SUM(dni2.qty)
						FROM `tabDelivery Note Item` dni2
						INNER JOIN `tabDelivery Note` dn2 ON dn2.name = dni2.parent
						WHERE dni2.batch_no = bi.batch_id
						AND dn2.docstatus = 1
						AND dn2.is_return = 1
					), 0)) -
					(COALESCE((
						SELECT SUM(dni3.qty)
						FROM `tabDelivery Note Item` dni3
						INNER JOIN `tabDelivery Note` dn3 ON dn3.name = dni3.parent
						WHERE dni3.batch_no = bi.batch_id
						AND dn3.docstatus = 1
						AND COALESCE(dn3.is_return, 0) = 0
					), 0) +
					COALESCE((
						SELECT SUM(pri2.qty)
						FROM `tabPurchase Receipt Item` pri2
						INNER JOIN `tabPurchase Receipt` pr2 ON pr2.name = pri2.parent
						WHERE pri2.batch_no = bi.batch_id
						AND pr2.docstatus = 1
						AND pr2.is_return = 1
					), 0)),
				2) AS stock,
				c.lot_no AS lot_no,
				COUNT(DISTINCT bi.batch_id) AS total_box,
				bi.cone AS cone,
				0 AS sort_order
			FROM `tabContainer` c
			INNER JOIN `tabBatch Items` bi ON bi.parent = c.name
			WHERE
				{where_clause}
			GROUP BY
				c.posting_date,
				c.container_no,
				c.lot_no,
				bi.cone,
				bi.item,
				c.pulp,
				c.lusture,
				c.glue,
				c.grade
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
