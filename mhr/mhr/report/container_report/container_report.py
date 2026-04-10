# Copyright (c) 2026, reformiqo and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import flt


PRECISION = 3


def execute(filters=None):
	columns = get_columns()
	data, total_row = get_data(filters)
	data.append(total_row)
	return columns, data


def get_columns():
	return [
		{"label": _("Date"), "fieldname": "date", "fieldtype": "Data", "width": 100},
		{"label": _("Container Number"), "fieldname": "container_number", "fieldtype": "Data", "width": 140},
		{"label": _("Item"), "fieldname": "item", "fieldtype": "Data", "width": 120},
		{"label": _("Pulp"), "fieldname": "pulp", "fieldtype": "Data", "width": 90},
		{"label": _("Lusture"), "fieldname": "lusture", "fieldtype": "Data", "width": 90},
		{"label": _("Glue"), "fieldname": "glue", "fieldtype": "Data", "width": 90},
		{"label": _("Grade"), "fieldname": "grade", "fieldtype": "Data", "width": 90},
		{"label": _("IN Qty"), "fieldname": "in_qty", "fieldtype": "Float", "width": 110, "precision": PRECISION},
		{"label": _("OUT Qty"), "fieldname": "out_qty", "fieldtype": "Float", "width": 110, "precision": PRECISION},
		{"label": _("Stock"), "fieldname": "stock", "fieldtype": "Float", "width": 110, "precision": PRECISION},
		{"label": _("Lot Number"), "fieldname": "lot_number", "fieldtype": "Data", "width": 110},
		{"label": _("Cone"), "fieldname": "cone", "fieldtype": "Data", "width": 80},
		{"label": _("Total Box"), "fieldname": "total_box", "fieldtype": "Int", "width": 100},
	]


def strip_prefix(val):
	if val and "-" in str(val):
		return str(val).rsplit("-", 1)[-1]
	return val or ""


def get_data(filters=None):
	query = """
		SELECT
			DATE_FORMAT(b.manufacturing_date, '%%%%d/%%%%m/%%%%Y') AS `date`,
			b.custom_container_no AS container_number,
			b.item AS item,
			b.custom_pulp AS pulp,
			b.custom_lusture AS lusture,
			b.custom_glue AS glue,
			b.custom_grade AS grade,
			ROUND(SUM(COALESCE(inward.total_in, 0)), {p}) AS in_qty,
			ROUND(SUM(COALESCE(outward.total_out, 0)), {p}) AS out_qty,
			CAST(
				ROUND(
					SUM(COALESCE(inward.total_in, 0)) - SUM(COALESCE(outward.total_out, 0)),
					{p}
				) AS DECIMAL(20,{p})
			) AS stock,
			b.custom_lot_no AS lot_number,
			b.custom_cone AS cone,
			COUNT(b.name) AS total_box
		FROM `tabBatch` AS b
		LEFT JOIN (
			SELECT
				sbe.batch_no,
				SUM(ABS(sbe.qty)) AS total_in
			FROM `tabSerial and Batch Entry` sbe
			INNER JOIN `tabSerial and Batch Bundle` sbb ON sbb.name = sbe.parent
				AND sbb.docstatus = 1
				AND sbb.type_of_transaction = 'Inward'
			GROUP BY sbe.batch_no
		) AS inward ON b.name = inward.batch_no
		LEFT JOIN (
			SELECT
				sbe.batch_no,
				SUM(ABS(sbe.qty)) AS total_out
			FROM `tabSerial and Batch Entry` sbe
			INNER JOIN `tabSerial and Batch Bundle` sbb ON sbb.name = sbe.parent
				AND sbb.docstatus = 1
				AND sbb.type_of_transaction = 'Outward'
			GROUP BY sbe.batch_no
		) AS outward ON b.name = outward.batch_no
		GROUP BY
			DATE_FORMAT(b.manufacturing_date, '%%%%d/%%%%m/%%%%Y'),
			b.custom_container_no,
			b.custom_lot_no,
			b.custom_cone,
			b.item,
			b.custom_pulp,
			b.custom_lusture,
			b.custom_glue,
			b.custom_grade
		ORDER BY
			b.custom_container_no ASC,
			b.custom_lot_no ASC,
			b.custom_cone ASC
	""".format(p=PRECISION)

	rows = frappe.db.sql(query, as_dict=True)

	# Strip prefixes from specification fields
	for row in rows:
		row["pulp"] = strip_prefix(row.get("pulp"))
		row["lusture"] = strip_prefix(row.get("lusture"))
		row["glue"] = strip_prefix(row.get("glue"))
		row["grade"] = strip_prefix(row.get("grade"))

	# Build total row with 3 precision
	total_in = round(sum(flt(r.get("in_qty", 0)) for r in rows), PRECISION)
	total_out = round(sum(flt(r.get("out_qty", 0)) for r in rows), PRECISION)
	total_stock = round(total_in - total_out, PRECISION)
	total_box = sum(r.get("total_box", 0) for r in rows)

	total_row = {
		"date": "",
		"container_number": "",
		"item": "Total",
		"pulp": "",
		"lusture": "",
		"glue": "",
		"grade": "",
		"in_qty": total_in,
		"out_qty": total_out,
		"stock": total_stock,
		"lot_number": "",
		"cone": "",
		"total_box": total_box,
	}

	return rows, total_row
