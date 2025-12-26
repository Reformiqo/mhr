// Copyright (c) 2025, reformiqo and contributors
// For license information, please see license.txt

frappe.query_reports["STOCK SHEET (BALANCE REPORT)"] = {
	"filters": [
		{
			"fieldname": "fdt",
			"label": __("From Date"),
			"fieldtype": "Date",
			"default": frappe.datetime.get_today(),
			"reqd": 1
		},
		{
			"fieldname": "tdt",
			"label": __("To Date"),
			"fieldtype": "Date",
			"default": frappe.datetime.get_today(),
			"reqd": 1
		},
		{
			"fieldname": "container",
			"label": __("Container"),
			"fieldtype": "Data"
		},
		{
			"fieldname": "lot_no",
			"label": __("Lot No"),
			"fieldtype": "Data"
		},
		{
			"fieldname": "cone",
			"label": __("Cone"),
			"fieldtype": "Data"
		}
	]
};
