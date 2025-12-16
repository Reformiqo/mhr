// Copyright (c) 2025, reformiqo and contributors
// For license information, please see license.txt

frappe.query_reports["STOCK SHEET (INWARD CONE WISE)"] = {
	"filters": [
		{
			"fieldname": "fdt",
			"fieldtype": "Date",
			"label": "From Date",
			"mandatory": 1,
			"default": frappe.datetime.get_today() - 30,
		},
		{
			"fieldname": "tdt",
			"fieldtype": "Date",
			"label": "To Date",
			"mandatory": 1,
			"default": frappe.datetime.get_today(),

		}

	]
};
