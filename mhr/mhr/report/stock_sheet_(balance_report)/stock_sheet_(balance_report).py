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
            "label": _("Balance"),
            "fieldname": "Balance",
            "fieldtype": "Data",
            "width": 100,
        },
        {
            "label": _("Lot Number"),
            "fieldname": "Lot Number",
            "fieldtype": "Data",
            "width": 120,
        },
        {
            "label": _("IN Box"),
            "fieldname": "IN Box",
            "fieldtype": "Data",
            "width": 100,
        },
        {
            "label": _("OUT Box"),
            "fieldname": "OUT Box",
            "fieldtype": "Data",
            "width": 100,
        },
        {
            "label": _("Balance Box"),
            "fieldname": "Balance Box",
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

    # Build WHERE conditions dynamically for Batch table
    where_conditions = ["b.creation BETWEEN %(fdt)s AND %(tdt)s"]

    if container:
        where_conditions.append("b.custom_container_no = %(container)s")

    if lot_no:
        where_conditions.append("b.custom_lot_no = %(lot_no)s")

    if cone:
        where_conditions.append("b.custom_cone = %(cone)s")

    where_clause = " AND ".join(where_conditions)

    # Use Stock Ledger Entry for accurate balance tracking
    # Balance = IN qty (positive actual_qty) - OUT qty (negative actual_qty)
    # IN Box = batches that received stock, OUT Box = batches that were delivered
    query = f"""
		WITH batch_data AS (
			SELECT
				b.name AS batch_id,
				b.item AS item,
				b.custom_container_no AS container_no,
				b.custom_lot_no AS lot_no,
				b.custom_cone AS cone,
				b.custom_pulp AS pulp,
				b.custom_lusture AS lusture,
				b.custom_glue AS glue,
				b.custom_grade AS grade,
				DATE(b.creation) AS batch_date
			FROM `tabBatch` b
			WHERE {where_clause}
		),
		sle_direct AS (
			-- Stock movements where batch_no is directly on SLE (older method)
			SELECT
				sle.batch_no AS batch_no,
				SUM(sle.actual_qty) AS balance,
				SUM(CASE WHEN sle.actual_qty > 0 THEN sle.actual_qty ELSE 0 END) AS in_qty,
				SUM(CASE WHEN sle.actual_qty < 0 THEN ABS(sle.actual_qty) ELSE 0 END) AS out_qty
			FROM `tabStock Ledger Entry` sle
			WHERE sle.docstatus = 1
			AND sle.is_cancelled = 0
			AND sle.batch_no IS NOT NULL
			AND sle.batch_no != ''
			AND (sle.serial_and_batch_bundle IS NULL OR sle.serial_and_batch_bundle = '')
			GROUP BY sle.batch_no
		),
		sle_bundle AS (
			-- Stock movements via Serial and Batch Bundle (ERPNext v15+ method)
			SELECT
				sbe.batch_no AS batch_no,
				SUM(sbe.qty) AS balance,
				SUM(CASE WHEN sbe.qty > 0 THEN sbe.qty ELSE 0 END) AS in_qty,
				SUM(CASE WHEN sbe.qty < 0 THEN ABS(sbe.qty) ELSE 0 END) AS out_qty
			FROM `tabSerial and Batch Entry` sbe
			INNER JOIN `tabSerial and Batch Bundle` sbb ON sbb.name = sbe.parent
			WHERE sbb.docstatus = 1
			AND sbb.is_cancelled = 0
			AND sbe.batch_no IS NOT NULL
			AND sbe.batch_no != ''
			GROUP BY sbe.batch_no
		),
		sle_data AS (
			-- Combine both sources
			SELECT
				batch_no,
				SUM(balance) AS balance,
				SUM(in_qty) AS in_qty,
				SUM(out_qty) AS out_qty
			FROM (
				SELECT * FROM sle_direct
				UNION ALL
				SELECT * FROM sle_bundle
			) combined
			GROUP BY batch_no
		),
		main_data_raw AS (
			SELECT
				DATE_FORMAT(bd.batch_date, '%%d/%%m/%%Y') AS report_date,
				bd.container_no AS container_no,
				bd.item AS item,
				bd.pulp AS pulp,
				bd.lusture AS lusture,
				bd.glue AS glue,
				bd.grade AS grade,
				ROUND(COALESCE(sd.balance, 0), 2) AS balance,
				bd.lot_no AS lot_no,
				COUNT(DISTINCT bd.batch_id) AS in_box,
				COUNT(DISTINCT CASE WHEN COALESCE(sd.out_qty, 0) > 0 THEN bd.batch_id END) AS out_box,
				COUNT(DISTINCT CASE WHEN COALESCE(sd.balance, 0) > 0 THEN bd.batch_id END) AS balance_box,
				bd.cone AS cone,
				0 AS sort_order
			FROM batch_data bd
			LEFT JOIN sle_data sd ON sd.batch_no = bd.batch_id
			GROUP BY
				bd.batch_date,
				bd.container_no,
				bd.lot_no,
				bd.cone,
				bd.item,
				bd.pulp,
				bd.lusture,
				bd.glue,
				bd.grade
		),
		main_data AS (
			SELECT * FROM main_data_raw
			WHERE CAST(COALESCE(cone, 0) AS SIGNED) > 0
				AND in_box > 0
				AND balance > 0
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
				SUM(balance) AS balance,
				lot_no,
				SUM(in_box) AS in_box,
				SUM(out_box) AS out_box,
				SUM(balance_box) AS balance_box,
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
				SUM(m.balance) AS balance,
				'' AS lot_no,
				SUM(m.in_box) AS in_box,
				SUM(m.out_box) AS out_box,
				SUM(m.balance_box) AS balance_box,
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
				THEN CONCAT('<span style="color:green;">', balance, '</span>')
				ELSE CONCAT('<b style="color:green;">', ROUND(balance, 2), '</b>')
			END AS `Balance`,
			lot_no AS `Lot Number`,
			CASE
				WHEN sort_order = 0
				THEN CONCAT('<span style="color:green;">', in_box, '</span>')
				ELSE CONCAT('<b style="color:green;">', in_box, '</b>')
			END AS `IN Box`,
			CASE
				WHEN sort_order = 0
				THEN CONCAT('<span style="color:red;">', out_box, '</span>')
				ELSE CONCAT('<b style="color:red;">', out_box, '</b>')
			END AS `OUT Box`,
			CASE
				WHEN sort_order = 0
				THEN CONCAT('<span style="color:green;">', balance_box, '</span>')
				ELSE CONCAT('<b style="color:green;">', balance_box, '</b>')
			END AS `Balance Box`,
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
