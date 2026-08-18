// Copyright (c) 2025, reformiqo and contributors
// For license information, please see license.txt

// Everything here hangs off a namespace rather than file-level `const` /
// `function` declarations: report scripts are eval'd into the page, and a
// top-level binding would be a global that clashes with the next script.
frappe.provide("mhr.balance_report");

// The Qty columns the grand-total row (sort_order 3) carries.
mhr.balance_report.TOTAL_FIELDS = [
	"Balance",
	"Balance Box",
	"Cone",
	"Booked Qty",
	"Buyer Qty",
	"Available Qty",
];

// Re-total the Qty columns over the rows a column filter left behind.
//
// The rows cannot simply be added up. MI1-I94 renders one FULL row per Sales
// Order booked against a stock group, and each of those rows repeats the
// group's Balance / Balance Box / Cone — summing them straight would count the
// same boxes two or three times. `_group_key` (set server-side) says which
// rendered rows came out of the same group, so:
//
//   * Balance / Balance Box / Cone  — taken once per group
//   * Booked Qty / Buyer Qty        — summed per row (each row is one booking)
//   * Available Qty                 — per group: balance minus the bookings
//                                     that survived the filter
//
// With no filter applied this reproduces the server's Step 7b figures exactly.
mhr.balance_report.totals_for = function (data, indices) {
	const groups = new Map();
	let booked = 0;
	let buyer = 0;

	indices.forEach((i) => {
		const row = data[i];
		// Detail rows only — the lot / container subtotals are already sums.
		if (!row || cint(row.sort_order) !== 0) return;

		const key = row._group_key == null ? "row:" + i : "grp:" + row._group_key;
		let group = groups.get(key);
		if (!group) {
			group = {
				balance: flt(row["Balance"]),
				box: flt(row["Balance Box"]),
				cone: cint(row["Cone"]),
				booked: 0,
			};
			groups.set(key, group);
		}
		group.booked += flt(row["Booked Qty"]);

		booked += flt(row["Booked Qty"]);
		buyer += flt(row["Buyer Qty"]);
	});

	let balance = 0;
	let box = 0;
	let cone = 0;
	let available = 0;
	groups.forEach((group) => {
		balance += group.balance;
		box += group.box;
		cone += group.cone;
		available += group.balance - group.booked;
	});

	return {
		"Balance": flt(balance, 2),
		"Balance Box": box,
		"Cone": cone,
		"Booked Qty": flt(booked, 2),
		"Buyer Qty": flt(buyer, 2),
		"Available Qty": flt(available, 2),
	};
};

// Keep the grand-total row on screen while a column filter is applied, and
// restate it over the rows that survived.
//
// The datatable's inline column filters run in the browser against the
// rendered cells, so the grand-total row — which leaves Lot Number, Container
// No, Grade etc. blank — matches nothing and disappears the moment a filter is
// typed into any of those columns. This is a different mechanism from the
// server-side Transaction Type filter fixed in MI1-I99.
mhr.balance_report.pin_grand_total = function (datatable, filters, indices) {
	const datamanager = datatable.datamanager;
	const data = (datamanager && datamanager.data) || [];

	const total_index = data.findIndex((row) => row && cint(row.sort_order) === 3);
	if (total_index === -1) return indices;

	// Stash what the server sent once per data load, so clearing the filter
	// restores those exact figures instead of a re-derived approximation.
	if (!datatable._mhr_total_baseline) {
		const baseline = {};
		mhr.balance_report.TOTAL_FIELDS.forEach((fieldname) => {
			baseline[fieldname] = data[total_index][fieldname];
		});
		datatable._mhr_total_baseline = baseline;
	}

	const values =
		filters && Object.keys(filters).length
			? mhr.balance_report.totals_for(data, indices)
			: datatable._mhr_total_baseline;

	mhr.balance_report.TOTAL_FIELDS.forEach((fieldname) => {
		// The row dict feeds the formatter; the cell feeds the renderer.
		data[total_index][fieldname] = values[fieldname];

		const col_index = datamanager.getColumnIndexById(fieldname);
		if (col_index > -1) {
			// html: null drops the cached render so the new content is
			// formatted rather than the stale HTML being reused.
			datamanager.updateCell(col_index, total_index, {
				content: values[fieldname],
				html: null,
			});
		}
	});

	return indices.indexOf(total_index) === -1 ? indices.concat(total_index) : indices;
};

frappe.query_reports["STOCK SHEET (BALANCE REPORT)"] = {
	"filters": [
		// From Date / To Date open blank on purpose (2026-08-18) — blank means
		// "every batch", which is the full stock position. Set either box to
		// narrow the range on Batch.creation.
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

	after_datatable_render: function (datatable) {
		if (!datatable || !datatable.datamanager) return;

		// Fires on every render, including a plain refresh that reuses the
		// instance — the stashed figures belong to the data just loaded.
		datatable._mhr_total_baseline = null;

		// The wrapper survives a refresh, so only install it once.
		if (datatable._mhr_filter_rows_patched) return;
		datatable._mhr_filter_rows_patched = true;

		// The stock filterRows, already wrapped by DataManager to return a
		// promise. Delegating to it keeps the built-in filter grammar
		// (`>`, `<`, `=`, `!=`, ranges, contains) exactly as it is.
		const base_filter_rows = datatable.datamanager.options.filterRows;

		datatable.datamanager.options.filterRows = function () {
			const filters = arguments[1];
			return Promise.resolve(base_filter_rows.apply(this, arguments)).then(
				function (indices) {
					// Nothing matched — leave the datatable's "No Data" state
					// alone rather than showing a lone total of zeros.
					if (!Array.isArray(indices) || !indices.length) return indices;
					try {
						return mhr.balance_report.pin_grand_total(
							datatable,
							filters,
							indices
						);
					} catch (e) {
						// Filtering must keep working even if the re-total
						// hits something unexpected.
						console.error(
							"STOCK SHEET (BALANCE REPORT): could not re-total the grand total row",
							e
						);
						return indices;
					}
				}
			);
		};
	},

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
