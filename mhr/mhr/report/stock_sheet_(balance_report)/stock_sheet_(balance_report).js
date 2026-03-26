// Copyright (c) 2025, reformiqo and contributors
// For license information, please see license.txt

frappe.query_reports["STOCK SHEET (BALANCE REPORT)"] = {
	"filters": [
		{
			"fieldname": "fdt",
			"label": __("From Date"),
			"fieldtype": "Date",
			"reqd": 0
		},
		{
			"fieldname": "tdt",
			"label": __("To Date"),
			"fieldtype": "Date",
			"reqd": 0
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
	],
	formatter: function(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (!data) return value;

		if (data.sort_order >= 1) {
			value = "<b>" + value + "</b>";
		}
		if (data.sort_order === 3) {
			value = "<b style='font-size:1.1em'>" + value + "</b>";
		}

		if (column.fieldname === "Balance" || column.fieldname === "Balance Box") {
			value = "<span style='color:green'>" + value + "</span>";
		}

		if (column.fieldname === "Booked Qty" && parseFloat(data["Booked Qty"]) > 0) {
			value = "<span style='color:orange'>" + value + "</span>";
		}

		if (column.fieldname === "Available Qty") {
			let avail = parseFloat(data["Available Qty"]);
			if (avail > 0) {
				value = "<span style='color:green'>" + value + "</span>";
			} else if (avail <= 0) {
				value = "<span style='color:red'>" + value + "</span>";
			}
		}

		return value;
	}
};
