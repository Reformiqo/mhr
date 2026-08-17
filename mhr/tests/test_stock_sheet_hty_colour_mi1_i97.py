"""MI1-I97 — Colour column on STOCK SHEET (BALANCE REPORT) in HTY mode.

No new data was required. create_batches() already folds the HTY specs into the
canonical Batch columns:

    Container.product -> Batch.custom_glue
    Container.colour  -> Batch.custom_lusture     <-- Colour lives here
    Container.type    -> Batch.custom_pulp

so the `Lusture` field has carried the Colour value for HTY all along. MI1-I64
relabelled Pulp -> Type and Glue -> Product for HTY but missed this third pair,
so the value rendered under a "Lusture" heading.

MI1-I97 labels it "Colour" and moves it to sit immediately after Grade, in HTY
mode only. VFY column output must be untouched.
"""

import importlib

import frappe
from frappe.tests.utils import FrappeTestCase

MODPATH = "mhr.mhr.report.stock_sheet_(balance_report).stock_sheet_(balance_report)"


def _labels(filters):
	mod = importlib.import_module(MODPATH)
	return [c["label"] for c in mod.get_columns(filters)]


def _by_label(filters):
	mod = importlib.import_module(MODPATH)
	return {c["label"]: c for c in mod.get_columns(filters)}


class TestHTYColourColumn(FrappeTestCase):
	def test_colour_column_exists_in_hty(self):
		self.assertIn("Colour", _labels({"transaction_type": "HTY"}))

	def test_colour_maps_to_the_lusture_field(self):
		"""The value is already in `Lusture`; this must not invent a new key or
		the column would render blank."""
		col = _by_label({"transaction_type": "HTY"})["Colour"]
		self.assertEqual(col["fieldname"], "Lusture")

	def test_colour_sits_immediately_after_grade(self):
		labels = _labels({"transaction_type": "HTY"})
		self.assertEqual(
			labels[labels.index("Grade") + 1], "Colour",
			"Colour must follow Grade directly in HTY.",
		)

	def test_lusture_heading_is_gone_in_hty(self):
		"""Emitted once, as Colour — not twice, and never under the old label."""
		labels = _labels({"transaction_type": "HTY"})
		self.assertNotIn("Lusture", labels)
		self.assertEqual(labels.count("Colour"), 1)

	def test_lusture_field_appears_exactly_once_in_hty(self):
		mod = importlib.import_module(MODPATH)
		fields = [c["fieldname"] for c in mod.get_columns({"transaction_type": "HTY"})]
		self.assertEqual(
			fields.count("Lusture"), 1,
			"Duplicate fieldnames make the datatable render one of them blank.",
		)

	def test_hty_spec_trio_is_now_complete(self):
		"""All three HTY spec labels, not just the two MI1-I64 caught."""
		labels = _labels({"transaction_type": "HTY"})
		for expected in ("Product", "Type", "Colour"):
			with self.subTest(label=expected):
				self.assertIn(expected, labels)
		for legacy in ("Glue", "Pulp", "Lusture"):
			with self.subTest(label=legacy):
				self.assertNotIn(legacy, labels)


class TestVFYColumnsUnchanged(FrappeTestCase):
	"""The hard rule: other transaction types remain unaffected."""

	EXPECTED_HEAD = [
		"Date", "Container No", "Item", "Lot Number", "Grade", "Cone",
		"Merge No", "Pulp", "Lusture", "Glue",
	]

	def test_vfy_column_order_is_unchanged(self):
		self.assertEqual(_labels({"transaction_type": "VFY"})[:10], self.EXPECTED_HEAD)

	def test_blank_filter_matches_vfy(self):
		"""No filter behaves as VFY — same as before MI1-I97."""
		self.assertEqual(_labels({}), _labels({"transaction_type": "VFY"}))

	def test_vfy_has_no_colour_column(self):
		self.assertNotIn("Colour", _labels({"transaction_type": "VFY"}))

	def test_vfy_keeps_lusture_in_its_original_slot(self):
		labels = _labels({"transaction_type": "VFY"})
		self.assertEqual(labels[labels.index("Pulp") + 1], "Lusture")

	def test_vfy_only_columns_still_present(self):
		labels = _labels({"transaction_type": "VFY"})
		for expected in ("Merge No", "Cross Section"):
			with self.subTest(label=expected):
				self.assertIn(expected, labels)


class TestColourValueRendersStripped(FrappeTestCase):
	"""End-to-end: an HTY container with a Colour must show it, prefix removed."""

	def test_hty_row_carries_the_colour_value(self):
		mod = importlib.import_module(MODPATH)

		batch = frappe.db.sql(
			"""
			SELECT custom_container_no, custom_lusture
			FROM `tabBatch`
			WHERE IFNULL(custom_transaction_type, '') = 'HTY'
			  AND custom_lusture LIKE 'Colour-%%'
			  AND IFNULL(custom_container_no, '') != ''
			LIMIT 1
			""",
			as_dict=True,
		)
		if not batch:
			self.skipTest("No HTY batch with a Colour- spec on this site.")

		container = batch[0]["custom_container_no"]
		expected = batch[0]["custom_lusture"].rsplit("-", 1)[-1]

		_cols, rows = mod.execute({"container": container, "transaction_type": "HTY"})
		detail = [r for r in rows if not r.get("sort_order")]
		if not detail:
			self.skipTest(f"Container {container} currently carries no stock balance.")

		self.assertIn(
			expected,
			[r.get("Lusture") for r in detail],
			f"Colour column must show {expected!r} (rendered from the Lusture field).",
		)
