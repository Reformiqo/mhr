"""MI1-I106 — a submitted Delivery Note opened reading "Not Saved".

Reported on MC-HTY-ST-DN01105: docstatus 1, nothing edited, yet the form
showed the orange "Not Saved" pill and an Update button.

The cause is in the `Delivery Note V2` Client Script. Its `refresh` handler
called `calculate_totals(frm)` unconditionally — drafts and submitted
documents alike — and `calculate_totals` wrote both totals back with
`frm.set_value`, which marks the form dirty for ANY difference:

    frm.set_value('custom_total_cone', total_cone);
    frm.set_value('total_qty', total_qty);

A JS float sum does not reproduce the number the server stored. That
Delivery Note's three rows are 500.3, 526.3 and 179.3; summed left to right
in IEEE-754 double that is 1205.8999999999999, while `total_qty` holds
1205.9. Different value -> `__unsaved = 1` -> "Not Saved" on a submitted
document. The cones (72 + 72 + 105) are integers, so they matched exactly
and only the quantity drifted — which is why this shows up on some Delivery
Notes and not others.

The sibling script `Total` had already been fixed for the same class of bug
(it guards on docstatus AND compares before writing); `Delivery Note V2` had
not. These tests pin both.
"""

import json

import frappe
from frappe.tests.utils import FrappeTestCase

SCRIPT_NAME = "Delivery Note V2"

# What the site actually held for this Client Script before MI1-I106.
# frappe/modules/import_file.py :: import_file_by_path skips a non-DocType
# record whose DB `modified` is >= the one in the JSON, so a fixture edit that
# forgets to move this forward is a silent no-op on exactly the sites that
# already have the script.
MODIFIED_BEFORE_THE_FIX = "2026-07-13 16:10:30.285445"


def _fixture_records():
	path = frappe.get_app_path("mhr", "fixtures", "client_script.json")
	with open(path, encoding="utf-8") as f:
		return json.load(f)


def _fixture_script(name, dt="Delivery Note"):
	for record in _fixture_records():
		if record.get("name") == name and record.get("dt") == dt:
			return record
	raise AssertionError(f"{name!r} ({dt}) is missing from client_script.json")


class TestTheDriftThatCausedIt(FrappeTestCase):
	"""Documents the arithmetic, so nobody 'simplifies' the guard away
	believing an equality check on floats is enough."""

	ROWS = (500.3, 526.3, 179.3)
	STORED_TOTAL_QTY = 1205.9

	def test_a_left_to_right_float_sum_misses_the_stored_total(self):
		total = 0.0
		for qty in self.ROWS:
			total += qty
		self.assertNotEqual(
			total,
			self.STORED_TOTAL_QTY,
			"If this ever passes, the float model changed and the comment in "
			"the Client Script needs revisiting — but the guard stays.",
		)

	def test_rounding_to_the_field_precision_makes_them_equal(self):
		"""The fix's other half: compare at the precision the field stores."""
		total = 0.0
		for qty in self.ROWS:
			total += qty
		self.assertEqual(round(total, 3), round(self.STORED_TOTAL_QTY, 3))

	def test_the_cones_never_drifted(self):
		"""72 + 72 + 105 is exact, which is why custom_total_cone was innocent
		and the report looked intermittent."""
		self.assertEqual(72.0 + 72.0 + 105.0, 249)


class TestCalculateTotalsIsGuarded(FrappeTestCase):
	def setUp(self):
		self.script = _fixture_script(SCRIPT_NAME)["script"]

	def test_it_returns_early_after_submit(self):
		self.assertIn(
			"function calculate_totals(frm) {\n    if (frm.doc.docstatus !== 0) return;",
			self.script,
			"calculate_totals must not recompute on a submitted or cancelled "
			"Delivery Note — the server settled those totals at submit time.",
		)

	def test_neither_total_is_written_directly_any_more(self):
		"""Both writes must go through the compare-first helper."""
		for fieldname in ("total_qty", "custom_total_cone"):
			with self.subTest(fieldname=fieldname):
				self.assertNotIn(
					f"frm.set_value('{fieldname}',",
					self.script,
					f"{fieldname} is being written directly again; a rounding "
					"artefact is enough to dirty the form.",
				)

	def test_the_helper_compares_at_the_stored_precision(self):
		for fragment in (
			"function mi1_i106_set_if_changed(frm, fieldname, value) {",
			"var precision = frm.precision(fieldname);",
			"var next = flt(value, precision);",
			"if (flt(frm.doc[fieldname], precision) === next) return;",
		):
			with self.subTest(fragment=fragment):
				self.assertIn(fragment, self.script)

	def test_both_totals_still_go_through_it(self):
		for fieldname in ("custom_total_cone", "total_qty"):
			with self.subTest(fieldname=fieldname):
				self.assertIn(
					f"mi1_i106_set_if_changed(frm, '{fieldname}', ",
					self.script,
					"A draft must still get its totals recalculated — this fix "
					"is about not writing when nothing changed, not about "
					"dropping the calculation.",
				)

	def test_refresh_still_calls_it(self):
		"""The guard belongs inside the function, not at one call site: the
		item handlers and the scan / fetch callbacks all reach it too."""
		self.assertIn("calculate_totals(frm);", self.script)


class TestTheFixtureWillActuallySync(FrappeTestCase):
	"""The MI1-I90 trap, in a different disguise."""

	def test_modified_moved_forward(self):
		from frappe.utils import get_datetime

		record = _fixture_script(SCRIPT_NAME)
		self.assertGreater(
			get_datetime(record["modified"]),
			get_datetime(MODIFIED_BEFORE_THE_FIX),
			"import_file_by_path skips a fixture record whose DB timestamp is "
			"not older than the JSON's, so this edit would never reach a site "
			"that already has the script.",
		)

	def test_the_script_is_still_enabled(self):
		self.assertEqual(_fixture_script(SCRIPT_NAME)["enabled"], 1)


class TestSiblingScriptStaysGuarded(FrappeTestCase):
	"""`Total` writes custom_total_cone as well. It already guards on
	docstatus and compares before writing — losing either would bring the
	same symptom back through the other door."""

	def setUp(self):
		self.script = _fixture_script("Total")["script"]

	def test_every_call_site_checks_docstatus(self):
		# The script's live half is everything after the commented-out first
		# version; both halves guard, so counting over the whole text is fine.
		self.assertEqual(
			self.script.count("calculate_total_cone(frm);"),
			self.script.count("if (frm.doc.docstatus === 0) {"),
			"A call to calculate_total_cone appeared without its docstatus "
			"guard.",
		)

	def test_it_compares_before_writing(self):
		self.assertIn(
			"if (parseFloat(frm.doc.custom_total_cone || 0) !== total_cone) {",
			self.script,
		)


class TestNoOtherDeliveryNoteScriptWritesTheTotals(FrappeTestCase):
	"""Whole-class guard: any enabled Delivery Note form script that writes
	either total must prove it thought about docstatus."""

	TOTALS = ("total_qty", "custom_total_cone")

	def test_every_writer_mentions_docstatus(self):
		for record in _fixture_records():
			if record.get("dt") != "Delivery Note" or record.get("view") != "Form":
				continue
			if not record.get("enabled"):
				continue

			script = record.get("script") or ""
			writes = [f for f in self.TOTALS if f"frm.set_value('{f}'" in script]
			if not writes:
				continue

			with self.subTest(script=record.get("name")):
				self.assertIn(
					"docstatus",
					script,
					f"{record.get('name')!r} writes {writes} but never looks at "
					"docstatus — on a submitted Delivery Note that is MI1-I106 "
					"again.",
				)
