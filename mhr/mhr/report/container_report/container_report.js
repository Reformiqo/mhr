// Copyright (c) 2026, reformiqo and contributors
// For license information, please see license.txt

frappe.query_reports["Container Report"] = {
	filters: [
		// MI1-I39 P2-C: HTY transaction_type filter. Blank = all (default).
		// VFY = only Containers whose transaction_type = 'VFY'.
		// HTY    = only Containers whose transaction_type = 'HTY'.
		{
			fieldname: "transaction_type",
			label: __("Transaction Type"),
			fieldtype: "Select",
			options: "\nVFY\nHTY",
			default: "",
		},
	],
};
