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
		},
		{
			"fieldname": "container",
			"fieldtype": "Data",
			"label": "Container",
			"mandatory": 0,
		},
		{
			"fieldname": "lot_no",
			"fieldtype": "Data",
			"label": "Lot No",
			"mandatory": 0,
		},
		{
			"fieldname": "cone",
			"fieldtype": "Data",
			"label": "Cone",
			"mandatory": 0,
		}
	],
	formatter: function(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (!data) return value;

		if (data.sort_order >= 1) {
			value = "<b>" + value + "</b>";
		}

		return value;
	}
};
