// Copyright (c) 2025, reformiqo and contributors
// For license information, please see license.txt

frappe.query_reports["STOCK SHEET (BALANCE REPORT)"] = {
	// MI1-I91 (2026-08-11): pre-fill the date range so the boxes are never
	// blank, WITHOUT changing which rows the report returns.
	//
	// fdt/tdt filter Batch.creation, so a fiscal-year default would hide every
	// batch created before 01-Apr — i.e. most of the stock actually on hand.
	// Seeding From Date with the earliest Batch on the site keeps the output
	// identical to the old blank-filter behaviour.
	//
	// Skipped entirely when a range already arrived from the route / a saved
	// filter, so drill-throughs and bookmarks are not overwritten.
	onload: function (report) {
		if (
			frappe.query_report.get_filter_value("fdt") ||
			frappe.query_report.get_filter_value("tdt")
		) {
			return;
		}
		frappe.call({
			method: "mhr.utilis.get_earliest_batch_date",
			callback: function (r) {
				// Both set in one call so the report refreshes once, not twice.
				frappe.query_report.set_filter_value({
					fdt: r.message || frappe.datetime.add_years(frappe.datetime.get_today(), -10),
					tdt: frappe.datetime.get_today(),
				});
			},
		});
	},

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
		},
		{
			"fieldname": "company",
			"label": __("Company"),
			"fieldtype": "Link",
			"options": "Company"
		},
		// MI1-I39 P2-C: HTY transaction_type filter. Blank = all.
		// MI1-I64 (rework 2): on_change forces report.refresh() so the
		// Pulp/Glue ↔ Type/Product header labels swap immediately when
		// the dropdown changes (otherwise headers stay stale until the
		// user clicks Refresh manually).
		{
			"fieldname": "transaction_type",
			"label": __("Transaction Type"),
			"fieldtype": "Select",
			"options": "\nVFY\nHTY",
			"default": "",
			on_change: function () {
				frappe.query_report.refresh();
			}
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
