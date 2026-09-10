// Copyright (c) 2026, reformiqo and contributors
// For license information, please see license.txt
//
// MI1-I135: a separate namespace from the original report's
// mhr.balance_report — report scripts are eval'd into the page, and this
// report's TOTAL_FIELDS is a different (longer) list, so sharing the
// original's namespace would let whichever report loads second silently
// overwrite the other's totals_for/TOTAL_FIELDS for the rest of the
// session.
frappe.provide("mhr.balance_report_v2");

// The Qty columns the grand-total row (sort_order 3) carries.
mhr.balance_report_v2.TOTAL_FIELDS = [
	"In Qty",
	"Out Qty",
	"GR Received",
	"Job work Send Qty",
	"Balance",
	"Balance Box",
	"Cone",
	"Booked Qty",
	"Buyer Qty",
	"Available Qty",
];

// Re-total the Qty columns over the rows a column filter left behind — same
// algorithm as the original report's mhr.balance_report.totals_for: In Qty /
// Out Qty / GR Received / Job work Send Qty / Balance / Balance Box / Cone
// are taken once per stock group (repeated identically across every Sales
// Order row that group expanded into); Booked Qty / Buyer Qty are summed per
// row (each row is one booking); Available Qty is derived per group.
mhr.balance_report_v2.totals_for = function (data, indices) {
	const groups = new Map();
	let booked = 0;
	let buyer = 0;

	indices.forEach((i) => {
		const row = data[i];
		if (!row || cint(row.sort_order) !== 0) return;

		const key = row._group_key == null ? "row:" + i : "grp:" + row._group_key;
		let group = groups.get(key);
		if (!group) {
			group = {
				in_qty: flt(row["In Qty"]),
				out_qty: flt(row["Out Qty"]),
				gr_qty: flt(row["GR Received"]),
				jw_qty: flt(row["Job work Send Qty"]),
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

	let in_qty = 0, out_qty = 0, gr_qty = 0, jw_qty = 0;
	let balance = 0;
	let box = 0;
	let cone = 0;
	let available = 0;
	groups.forEach((group) => {
		in_qty += group.in_qty;
		out_qty += group.out_qty;
		gr_qty += group.gr_qty;
		jw_qty += group.jw_qty;
		balance += group.balance;
		box += group.box;
		cone += group.cone;
		available += group.balance - group.booked;
	});

	return {
		"In Qty": flt(in_qty, 2),
		"Out Qty": flt(out_qty, 2),
		"GR Received": flt(gr_qty, 2),
		"Job work Send Qty": flt(jw_qty, 2),
		"Balance": flt(balance, 2),
		"Balance Box": box,
		"Cone": cone,
		"Booked Qty": flt(booked, 2),
		"Buyer Qty": flt(buyer, 2),
		"Available Qty": flt(available, 2),
	};
};

// Keep the grand-total row on screen while a column filter is applied, and
// restate it over the rows that survived — same mechanism as the original
// report (see its .js for the full rationale).
mhr.balance_report_v2.pin_grand_total = function (datatable, filters, indices) {
	const datamanager = datatable.datamanager;
	const data = (datamanager && datamanager.data) || [];

	const total_index = data.findIndex((row) => row && cint(row.sort_order) === 3);
	if (total_index === -1) return indices;

	if (!datatable._mhr_total_baseline) {
		const baseline = {};
		mhr.balance_report_v2.TOTAL_FIELDS.forEach((fieldname) => {
			baseline[fieldname] = data[total_index][fieldname];
		});
		datatable._mhr_total_baseline = baseline;
	}

	const values =
		filters && Object.keys(filters).length
			? mhr.balance_report_v2.totals_for(data, indices)
			: datatable._mhr_total_baseline;

	mhr.balance_report_v2.TOTAL_FIELDS.forEach((fieldname) => {
		data[total_index][fieldname] = values[fieldname];

		const col_index = datamanager.getColumnIndexById(fieldname);
		if (col_index > -1) {
			datamanager.updateCell(col_index, total_index, {
				content: values[fieldname],
				html: null,
			});
		}
	});

	return indices.indexOf(total_index) === -1 ? indices.concat(total_index) : indices;
};

frappe.query_reports["STOCK SHEET (BALANCE REPORT) v2"] = {
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

		datatable._mhr_total_baseline = null;

		if (datatable._mhr_filter_rows_patched) return;
		datatable._mhr_filter_rows_patched = true;

		const base_filter_rows = datatable.datamanager.options.filterRows;

		datatable.datamanager.options.filterRows = function () {
			const filters = arguments[1];
			return Promise.resolve(base_filter_rows.apply(this, arguments)).then(
				function (indices) {
					if (!Array.isArray(indices) || !indices.length) return indices;
					try {
						return mhr.balance_report_v2.pin_grand_total(
							datatable,
							filters,
							indices
						);
					} catch (e) {
						console.error(
							"STOCK SHEET (BALANCE REPORT) v2: could not re-total the grand total row",
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
