// Copyright (c) 2025, reformiqo and contributors
// For license information, please see license.txt

frappe.query_reports["STOCK SHEETS (INWARD REST STOCK )"] = {
	"filters": [
		{
			"fieldname": "fdt",
			"fieldtype": "Date",
			"label": "From Date",
			"mandatory": 1,
			"wildcard_filter": 0
		},
		{
			"fieldname": "tdt",
			"fieldtype": "Date",
			"label": "To Date",
			"mandatory": 1,
			"wildcard_filter": 0
		}
	]
};
