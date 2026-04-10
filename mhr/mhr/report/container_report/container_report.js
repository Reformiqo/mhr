// Copyright (c) 2026, reformiqo and contributors
// For license information, please see license.txt

frappe.query_reports["Container Report"] = {
	filters: [],

	formatter: function (value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (data && data.is_total_row) {
			value = "<b>" + value + "</b>";
		}
		return value;
	},

	after_datatable_render: function (datatable) {
		if (!datatable || !datatable.bodyScrollable) return;

		// Find the last row and make it sticky
		const rows = datatable.bodyScrollable.querySelectorAll(".dt-row");
		if (!rows.length) return;

		const lastRow = rows[rows.length - 1];

		// Verify it's the total row
		const cells = lastRow.querySelectorAll(".dt-cell__content");
		let isTotalRow = false;
		cells.forEach(function (cell) {
			if (cell.innerHTML.includes("<b>") && cell.textContent.trim() === "Total") {
				isTotalRow = true;
			}
		});

		if (!isTotalRow) return;

		lastRow.style.position = "sticky";
		lastRow.style.bottom = "0";
		lastRow.style.zIndex = "2";
		lastRow.style.backgroundColor = "#f5f5f5";
		lastRow.style.borderTop = "2px solid #d1d8dd";
	},
};
