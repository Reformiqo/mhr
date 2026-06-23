// Copyright (c) 2026, reformiqo and contributors
// For license information, please see license.txt

frappe.query_reports["Container Report"] = {
	filters: [
		// MI1-I39 P2-C: HTY transaction_type filter. Blank = all (default).
		// VFY = only Containers whose transaction_type = 'VFY'.
		// HTY = only Containers whose transaction_type = 'HTY'.
		//
		// MI1-I64 (rework 2): on_change must call report.refresh() so the
		// dropdown immediately re-runs execute() — which re-fetches columns
		// AND data. Without this, changing the dropdown updates the URL
		// but the header row keeps showing whatever the previous run
		// returned (Pulp/Glue stuck after toggling to HTY, or vice versa).
		{
			fieldname: "transaction_type",
			label: __("Transaction Type"),
			fieldtype: "Select",
			options: "\nVFY\nHTY",
			default: "",
			on_change: function () {
				frappe.query_report.refresh();
			},
		},
	],
};
