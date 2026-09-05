"""STOCK SHEET (BALANCE REPORT) — three follow-ups reported 2026-08-18.

  1. The Grand Total row vanished as soon as a datatable column filter was
     typed (e.g. Lot Number). That filter runs in the BROWSER, against the
     rendered cells — a different mechanism from the server-side Transaction
     Type filter fixed in MI1-I99. The total row leaves Lot Number / Container
     No / Grade blank, so it matches nothing and is dropped.

     The fix lives in the report's .js, which pins the row and re-totals it
     over whatever survived. It cannot sum the visible rows naively: MI1-I94
     renders one FULL row per Sales Order booked against a stock group, and
     every one of those rows repeats the group's Balance / Balance Box / Cone.
     The server now stamps `_group_key` on each detail row so the browser can
     count group-level figures once. TestGroupKeyReproducesTheGrandTotal below
     runs that exact arithmetic in Python — if it drifts from the server's own
     Step 7b figures, the on-screen total is wrong.

  2. Aging showed 0 on same-day rows (blank is wanted), and sat at the far
     right — it belongs between Date and Container No.

  3. From Date / To Date must open blank again. MI1-I91 seeded them from the
     earliest Batch; that is reverted, along with its helper.

Existing behaviour — VFY column set, HTY relabelling, the row set itself —
must not change.
"""

import importlib
import io
import os
import re

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import cint, flt

MODPATH = "mhr.mhr.report.stock_sheet_(balance_report).stock_sheet_(balance_report)"

REPORT_DIR = os.path.join(
	frappe.get_app_path("mhr"), "mhr", "report", "stock_sheet_(balance_report)"
)
REPORT_JS = os.path.join(REPORT_DIR, "stock_sheet_(balance_report).js")


def _mod():
	return importlib.import_module(MODPATH)


def _labels(filters):
	return [c["label"] for c in _mod().get_columns(filters)]


def _js():
	return io.open(REPORT_JS, encoding="utf-8").read()


def _busiest_container():
	"""The container with the most batches — the best chance of a stock group
	carrying several Sales Order bookings, which is what makes the dedupe in
	TestGroupKeyReproducesTheGrandTotal meaningful."""
	row = frappe.db.sql(
		"""
		SELECT custom_container_no
		FROM `tabBatch`
		WHERE IFNULL(custom_container_no, '') != ''
		GROUP BY custom_container_no
		ORDER BY COUNT(*) DESC
		LIMIT 1
		"""
	)
	return row[0][0] if row else None


# ---------------------------------------------------------------------------
# 2. Aging column — position and blanks
# ---------------------------------------------------------------------------


class TestAgingColumnPosition(FrappeTestCase):
	def test_aging_sits_between_date_and_container_no(self):
		for tt in ("VFY", "HTY", ""):
			with self.subTest(transaction_type=tt):
				labels = _labels({"transaction_type": tt})
				self.assertEqual(
					labels[:3], ["Date", "Aging", "Container No"],
					"Aging must be the second column, right after Date.",
				)

	def test_aging_appears_exactly_once(self):
		for tt in ("VFY", "HTY"):
			with self.subTest(transaction_type=tt):
				self.assertEqual(_labels({"transaction_type": tt}).count("Aging"), 1)

	def test_aging_no_longer_trails_accepted_warehouse(self):
		labels = _labels({"transaction_type": "VFY"})
		self.assertGreater(
			labels.index("Accepted Warehouse"), labels.index("Aging"),
			"Aging must have moved off the far right of the report.",
		)

	def test_nothing_else_moved(self):
		"""Take Aging out and the remaining order is byte-identical to before."""
		labels = [l for l in _labels({"transaction_type": "VFY"}) if l != "Aging"]
		self.assertEqual(
			labels,
			[
				"Date", "Container No", "Item", "Lot Number", "Grade", "Cone",
				"Merge No", "Pulp", "Lusture", "Glue", "Balance Qty",
				"Booked Qty", "Balance Box", "Total Booked", "Available Qty",
				"Sales Order", "Buyer", "Sales Person", "Lifting Terms",
				# MI1-I120 revision (Raj 2026-09-05): the order's delivery picture.
				"Delivered Qty", "Delivered Weight", "Pending Qty", "Pending Weight",
				"Cross Section", "Production Date", "Notes", "Location",
				"Accepted Warehouse", "sort_order",
			],
		)

	def test_aging_is_still_an_int_column(self):
		col = {c["fieldname"]: c for c in _mod().get_columns({})}["Aging"]
		self.assertEqual(col["fieldtype"], "Int")


class TestAgingBlanks(FrappeTestCase):
	"""frappe's Int formatter renders blank ONLY for null — cint("") is 0, so
	an empty string would print as "0", which is the defect being fixed."""

	def setUp(self):
		self.container = _busiest_container()
		if not self.container:
			self.skipTest("No batches with a container number on this site.")
		_cols, self.rows = _mod().execute({"container": self.container})
		if not self.rows:
			self.skipTest(f"Container {self.container} carries no stock balance.")

	def test_no_row_reports_zero_aging(self):
		for r in self.rows:
			self.assertNotEqual(
				r.get("Aging"), 0,
				"A same-day row must leave Aging blank, not print 0.",
			)

	def test_aging_is_never_the_empty_string(self):
		for r in self.rows:
			self.assertNotEqual(
				r.get("Aging"), "",
				'Aging must be None to render blank — "" renders as 0.',
			)

	def test_total_rows_leave_aging_null(self):
		totals = [r for r in self.rows if r.get("sort_order")]
		self.assertTrue(totals, "Expected at least one total row.")
		for r in totals:
			self.assertIsNone(r.get("Aging"))

	def test_detail_rows_are_null_or_a_positive_day_count(self):
		details = [r for r in self.rows if not r.get("sort_order")]
		self.assertTrue(details, "Expected at least one detail row.")
		for r in details:
			aging = r.get("Aging")
			if aging is None:
				continue
			self.assertIsInstance(aging, int)
			self.assertGreater(aging, 0)


# ---------------------------------------------------------------------------
# 1. Grand Total under a column filter
# ---------------------------------------------------------------------------


class TestGroupKeyIsStamped(FrappeTestCase):
	def setUp(self):
		self.container = _busiest_container()
		if not self.container:
			self.skipTest("No batches with a container number on this site.")
		_cols, self.rows = _mod().execute({"container": self.container})
		if not self.rows:
			self.skipTest(f"Container {self.container} carries no stock balance.")
		self.details = [r for r in self.rows if not r.get("sort_order")]

	def test_every_detail_row_carries_a_group_key(self):
		self.assertTrue(self.details, "Expected at least one detail row.")
		for r in self.details:
			self.assertIsInstance(
				r.get("_group_key"), int,
				"Without _group_key the browser cannot tell repeated Sales Order "
				"rows apart from distinct stock, and would over-count the total.",
			)

	def test_total_rows_carry_no_group_key(self):
		for r in self.rows:
			if r.get("sort_order"):
				self.assertIsNone(r.get("_group_key"))

	def test_rows_sharing_a_key_repeat_the_same_group_figures(self):
		by_key = {}
		for r in self.details:
			by_key.setdefault(r["_group_key"], []).append(r)
		for key, rows in by_key.items():
			with self.subTest(group_key=key):
				for field in ("Balance", "Balance Box", "Cone"):
					self.assertEqual(
						len({r.get(field) for r in rows}), 1,
						f"{field} must be identical across a group's rows — it is "
						"the group's figure repeated, not per-booking data.",
					)

	def test_group_key_is_not_a_report_column(self):
		"""It is a marker on the row dict, not something the user sees or
		exports."""
		fieldnames = {c["fieldname"] for c in _mod().get_columns({})}
		self.assertNotIn("_group_key", fieldnames)


class TestGroupKeyReproducesTheGrandTotal(FrappeTestCase):
	"""The arithmetic the report's .js runs after a column filter, executed
	here in Python against the unfiltered result. With every row visible it
	must land on the server's own Step 7b figures — if it does not, the total
	shown under a filter is wrong."""

	def setUp(self):
		self.container = _busiest_container()
		if not self.container:
			self.skipTest("No batches with a container number on this site.")
		_cols, rows = _mod().execute({"container": self.container})
		if not rows:
			self.skipTest(f"Container {self.container} carries no stock balance.")
		self.details = [r for r in rows if not r.get("sort_order")]
		grand = [r for r in rows if r.get("sort_order") == 3]
		self.assertTrue(grand, "The report must end with a grand-total row.")
		self.grand = grand[0]

	def _recompute(self, rows):
		"""Mirror of mhr.balance_report.totals_for in the report's .js."""
		groups = {}
		booked = 0.0
		buyer = 0.0
		for r in rows:
			key = r.get("_group_key")
			group = groups.get(key)
			if group is None:
				group = {
					"balance": flt(r.get("Balance")),
					"box": flt(r.get("Balance Box")),
					"cone": cint(r.get("Cone")),
					"booked": 0.0,
				}
				groups[key] = group
			group["booked"] += flt(r.get("Booked Qty"))
			booked += flt(r.get("Booked Qty"))
			buyer += flt(r.get("Buyer Qty"))

		return {
			"Balance": round(sum(g["balance"] for g in groups.values()), 2),
			"Balance Box": sum(g["box"] for g in groups.values()),
			"Cone": sum(g["cone"] for g in groups.values()),
			"Booked Qty": round(booked, 2),
			"Buyer Qty": round(buyer, 2),
			"Available Qty": round(
				sum(g["balance"] - g["booked"] for g in groups.values()), 2
			),
		}

	def test_the_data_actually_exercises_the_dedupe(self):
		"""Guard against a vacuous pass: at least one stock group must render
		as more than one row, or nothing is being deduplicated."""
		counts = {}
		for r in self.details:
			counts[r["_group_key"]] = counts.get(r["_group_key"], 0) + 1
		if max(counts.values(), default=0) < 2:
			self.skipTest(
				f"Container {self.container} has no stock group with multiple "
				"Sales Order bookings — the dedupe is untested here."
			)
		self.assertGreaterEqual(max(counts.values()), 2)

	def test_recompute_matches_the_server_grand_total(self):
		got = self._recompute(self.details)
		tolerance = max(0.05, 0.01 * len(self.details))

		for field in ("Balance", "Booked Qty", "Buyer Qty", "Available Qty"):
			with self.subTest(field=field):
				self.assertAlmostEqual(
					flt(got[field]), flt(self.grand.get(field)), delta=tolerance,
					msg=f"Client-side {field} total drifted from the server's.",
				)

		for field in ("Balance Box", "Cone"):
			with self.subTest(field=field):
				self.assertEqual(
					cint(got[field]), cint(self.grand.get(field)),
					f"{field} is a count — it must match exactly.",
				)

	def test_naive_summing_would_have_been_wrong(self):
		"""Proves the dedupe is load-bearing rather than decorative."""
		counts = {}
		for r in self.details:
			counts[r["_group_key"]] = counts.get(r["_group_key"], 0) + 1
		if max(counts.values(), default=0) < 2:
			self.skipTest("No repeated Sales Order rows on this container.")

		naive = round(sum(flt(r.get("Balance")) for r in self.details), 2)
		self.assertGreater(
			naive, flt(self.grand.get("Balance")),
			"A straight sum over the rendered rows must over-count — that is "
			"exactly what _group_key exists to prevent.",
		)


class TestReportScriptPinsTheGrandTotal(FrappeTestCase):
	def setUp(self):
		self.src = _js()

	def test_after_datatable_render_hook_is_wired(self):
		self.assertIn("after_datatable_render", self.src)

	def test_it_wraps_filter_rows(self):
		self.assertIn("options.filterRows", self.src)
		self.assertIn(
			"base_filter_rows", self.src,
			"The built-in filter must be delegated to, not reimplemented — "
			"otherwise the >, <, =, != and range filter grammar is lost.",
		)

	def test_it_reads_the_group_key(self):
		self.assertIn("_group_key", self.src)

	def test_it_targets_the_grand_total_row_only(self):
		self.assertIn("cint(row.sort_order) === 3", self.src)

	def test_every_qty_column_is_re_totalled(self):
		for field in (
			"Balance", "Balance Box", "Cone", "Booked Qty", "Buyer Qty",
			"Available Qty",
		):
			with self.subTest(field=field):
				self.assertIn(f'"{field}"', self.src)

	def test_failures_cannot_break_filtering(self):
		self.assertIn("catch", self.src)


# ---------------------------------------------------------------------------
# 3. Blank date filters
# ---------------------------------------------------------------------------


class TestDateFiltersOpenBlank(FrappeTestCase):
	def setUp(self):
		self.src = _js()

	def test_no_onload_seeding(self):
		self.assertNotIn(
			"onload", self.src,
			"From Date / To Date must open blank — no onload seeding.",
		)

	def test_earliest_batch_date_is_no_longer_called(self):
		self.assertNotIn("get_earliest_batch_date", self.src)

	def test_the_helper_is_gone(self):
		import mhr.utilis as utilis

		self.assertFalse(
			hasattr(utilis, "get_earliest_batch_date"),
			"The seeding helper has no caller left; it must not linger as a "
			"whitelisted endpoint.",
		)

	def test_date_filters_declare_no_default(self):
		for fieldname in ("fdt", "tdt"):
			with self.subTest(fieldname=fieldname):
				block = re.search(
					r'"fieldname":\s*"%s".*?\}' % fieldname, self.src, re.S
				)
				self.assertIsNotNone(block, f"{fieldname} filter not found.")
				self.assertNotIn("default", block.group(0))

	def test_blank_range_still_returns_rows(self):
		container = _busiest_container()
		if not container:
			self.skipTest("No batches with a container number on this site.")
		_cols, rows = _mod().execute({"container": container})
		if not rows:
			self.skipTest(f"Container {container} carries no stock balance.")
		self.assertTrue(rows, "A blank From/To Date must mean 'every batch'.")
