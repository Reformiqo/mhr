// Copyright (c) 2026, reformiqo and contributors
// For license information, please see license.txt

frappe.query_reports["Container Report"] = {
	filters: [],

	after_datatable_render: function (datatable) {
		if (!datatable || !datatable.bodyScrollable) return;

		const rows = datatable.bodyScrollable.querySelectorAll(".dt-row");
		if (!rows.length) return;

		const lastRow = rows[rows.length - 1];
		const cells = lastRow.querySelectorAll(".dt-cell__content");
		let isTotalRow = false;
		cells.forEach(function (cell) {
			if (cell.textContent.trim() === "Total") {
				isTotalRow = true;
			}
		});

		if (!isTotalRow) return;

		// Bold all cells
		cells.forEach(function (cell) {
			cell.style.fontWeight = "bold";
		});

		// Make sticky at bottom
		lastRow.style.position = "sticky";
		lastRow.style.bottom = "0";
		lastRow.style.zIndex = "2";
		lastRow.style.backgroundColor = "#f5f5f5";
		lastRow.style.borderTop = "2px solid #d1d8dd";
	},
};
