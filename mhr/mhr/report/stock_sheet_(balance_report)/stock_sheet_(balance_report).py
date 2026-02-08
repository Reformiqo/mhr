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
            "label": _("Balance Box"),
            "fieldname": "Balance Box",
            "fieldtype": "Data",
            "width": 100,
        },
        {"label": _("Cone"), "fieldname": "Cone", "fieldtype": "Data", "width": 100},
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
				DATE(b.creation) AS batch_date,
				b.batch_qty AS net_weight
			FROM `tabBatch` b
			WHERE {where_clause}
		),
		sle_direct AS (
			SELECT
				sle.batch_no AS batch_no,
				SUM(sle.actual_qty) AS balance,
				SUM(CASE WHEN sle.actual_qty > 0 THEN sle.actual_qty ELSE 0 END) AS in_qty,
				SUM(CASE WHEN sle.actual_qty < 0 THEN ABS(sle.actual_qty) ELSE 0 END) AS out_qty
			FROM `tabStock Ledger Entry` sle
			WHERE sle.docstatus = 1
			AND sle.is_cancelled = 0
			AND sle.batch_no IN (SELECT batch_id FROM batch_data)
			AND (sle.serial_and_batch_bundle IS NULL OR sle.serial_and_batch_bundle = '')
			GROUP BY sle.batch_no
		),
		sle_bundle AS (
			SELECT
				sbe.batch_no AS batch_no,
				SUM(sbe.qty) AS balance,
				SUM(CASE WHEN sbe.qty > 0 THEN sbe.qty ELSE 0 END) AS in_qty,
				SUM(CASE WHEN sbe.qty < 0 THEN ABS(sbe.qty) ELSE 0 END) AS out_qty
			FROM `tabSerial and Batch Entry` sbe
			INNER JOIN `tabSerial and Batch Bundle` sbb ON sbb.name = sbe.parent
			WHERE sbb.docstatus = 1
			AND sbb.is_cancelled = 0
			AND sbe.batch_no IN (SELECT batch_id FROM batch_data)
			GROUP BY sbe.batch_no
		),
		sle_data AS (
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
				bd.batch_date AS raw_date,
				bd.container_no AS container_no,
				bd.item AS item,
				bd.pulp AS pulp,
				bd.lusture AS lusture,
				bd.glue AS glue,
				bd.grade AS grade,
				ROUND(SUM(CASE WHEN COALESCE(sd.balance, 0) > 0 THEN bd.net_weight ELSE 0 END), 2) AS balance,
				bd.lot_no AS lot_no,
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
				AND balance_box > 0
				AND balance > 0
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
				SUM(balance) AS balance,
				lot_no,
				SUM(balance_box) AS balance_box,
				'' AS cone,
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
				SUM(m.balance) AS balance,
				'' AS lot_no,
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
			ROUND(balance, 2) AS `Balance`,
			lot_no AS `Lot Number`,
			balance_box AS `Balance Box`,
			cone AS `Cone`,
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

    # Post-process: strip Item Specification prefixes (e.g. "SPEC-value" -> "value")
    for row in data:
        for field in ("Pulp", "Lusture", "Glue", "Grade"):
            val = row.get(field)
            if val and "-" in str(val):
                row[field] = str(val).rsplit("-", 1)[-1]

    return data
