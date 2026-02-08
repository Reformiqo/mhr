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
        {
            "label": _("sort_order"),
            "fieldname": "sort_order",
            "fieldtype": "Int",
            "width": 0,
            "hidden": 1,
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

    if not fdt or not tdt:
        frappe.throw(_("Please select From Date and To Date"))

    # Build WHERE conditions dynamically for Batch table
    where_conditions = ["b.creation BETWEEN %(fdt)s AND %(tdt)s"]

    if container:
        where_conditions.append("b.custom_container_no = %(container)s")

    if lot_no:
        where_conditions.append("b.custom_lot_no = %(lot_no)s")

    where_clause = " AND ".join(where_conditions)

    query = f"""
		WITH batch_data AS (
			SELECT
				b.name AS batch_id,
				b.item AS item,
				b.custom_container_no AS container_no,
				b.custom_lot_no AS lot_no,
				b.custom_pulp AS pulp,
				b.custom_lusture AS lusture,
				b.custom_glue AS glue,
				b.custom_grade AS grade,
				DATE(b.creation) AS batch_date
			FROM `tabBatch` b
			WHERE {where_clause}
		),
		out_qty_direct AS (
			SELECT DISTINCT
				dni.batch_no AS batch_no
			FROM `tabDelivery Note Item` dni
			INNER JOIN `tabDelivery Note` dn ON dn.name = dni.parent
			WHERE dn.docstatus = 1
			AND dni.batch_no IN (SELECT batch_id FROM batch_data)
			AND (dni.serial_and_batch_bundle IS NULL OR dni.serial_and_batch_bundle = '')
		),
		out_qty_bundle AS (
			SELECT DISTINCT
				sbe.batch_no AS batch_no
			FROM `tabSerial and Batch Entry` sbe
			INNER JOIN `tabSerial and Batch Bundle` sbb ON sbb.name = sbe.parent
			INNER JOIN `tabDelivery Note Item` dni ON dni.serial_and_batch_bundle = sbb.name
			INNER JOIN `tabDelivery Note` dn ON dn.name = dni.parent
			WHERE dn.docstatus = 1
			AND sbb.docstatus = 1
			AND sbb.is_cancelled = 0
			AND sbe.batch_no IN (SELECT batch_id FROM batch_data)
		),
		out_batches AS (
			SELECT DISTINCT batch_no
			FROM (
				SELECT * FROM out_qty_direct
				UNION ALL
				SELECT * FROM out_qty_bundle
			) combined
		),
		main_data AS (
			SELECT
				DATE_FORMAT(MIN(bd.batch_date), '%%d/%%m/%%Y') AS report_date,
				MIN(bd.batch_date) AS raw_date,
				bd.container_no AS container_no,
				bd.item AS item,
				bd.pulp AS pulp,
				bd.lusture AS lusture,
				bd.glue AS glue,
				bd.grade AS grade,
				COUNT(DISTINCT bd.batch_id) AS in_qty,
				COUNT(DISTINCT ob.batch_no) AS out_qty,
				COUNT(DISTINCT bd.batch_id) - COUNT(DISTINCT ob.batch_no) AS stock,
				bd.lot_no AS lot_no,
				COUNT(DISTINCT bd.batch_id) AS total_box,
				0 AS sort_order
			FROM batch_data bd
			LEFT JOIN out_batches ob ON ob.batch_no = bd.batch_id
			GROUP BY
				bd.container_no,
				bd.lot_no,
				bd.item,
				bd.pulp,
				bd.lusture,
				bd.glue,
				bd.grade
		),
		lot_total AS (
			SELECT
				report_date,
				raw_date,
				container_no,
				CAST(COUNT(item) AS CHAR) AS item,
				'' AS pulp,
				'' AS lusture,
				'Total:' AS glue,
				'' AS grade,
				SUM(in_qty) AS in_qty,
				SUM(out_qty) AS out_qty,
				ROUND(SUM(stock),2) AS stock,
				lot_no,
				SUM(total_box) AS total_box,
				1 AS sort_order
			FROM main_data
			GROUP BY
				report_date,
				raw_date,
				container_no,
				lot_no
		),
		container_lot_count AS (
			SELECT
				report_date,
				raw_date,
				container_no,
				COUNT(DISTINCT lot_no) AS lot_count
			FROM main_data
			GROUP BY
				report_date,
				raw_date,
				container_no
		),
		container_total AS (
			SELECT
				m.report_date,
				m.raw_date,
				m.container_no,
				CAST(COUNT(m.item) AS CHAR) AS item,
				'' AS pulp,
				'' AS lusture,
				'Grand Total:' AS glue,
				'' AS grade,
				SUM(m.in_qty) AS in_qty,
				SUM(m.out_qty) AS out_qty,
				ROUND(SUM(m.stock),2) AS stock,
				'' AS lot_no,
				SUM(m.total_box) AS total_box,
				2 AS sort_order
			FROM main_data m
			INNER JOIN container_lot_count c
				ON m.report_date = c.report_date
				AND m.container_no = c.container_no
			WHERE c.lot_count > 1
			GROUP BY
				m.report_date,
				m.raw_date,
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
			in_qty AS `IN Qty`,
			out_qty AS `OUT Qty`,
			stock AS `Stock`,
			lot_no AS `Lot Number`,
			total_box AS `Total Box`,
			sort_order AS `sort_order`
		FROM (
			SELECT * FROM main_data
			UNION ALL
			SELECT * FROM lot_total
			UNION ALL
			SELECT * FROM container_total
		) final
		ORDER BY
			raw_date DESC,
			container_no,
			CASE WHEN lot_no = '' THEN 'ZZZZZ' ELSE lot_no END,
			sort_order
	"""

    params = {"fdt": fdt, "tdt": tdt}

    if container:
        params["container"] = container

    if lot_no:
        params["lot_no"] = lot_no

    data = frappe.db.sql(query, params, as_dict=1)
    return data
