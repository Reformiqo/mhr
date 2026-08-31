"""Delivery Note Total Quantity stayed 0 when rows were added by script.

`frm.add_child()` fires no grid event, so neither `items_add` nor ERPNext's
`calculate_taxes_and_totals` runs — and both of those are what normally set
`total_qty` (erpnext taxes_and_totals.js:361, taxes_and_totals.py:395).

Frappe concatenates every enabled Form Client Script for a DocType into one
blob and evaluates it together (frappe/desk/form/meta.py :: add_custom_script),
so they share scope: `calculate_totals`, declared in `Delivery Note V2`, is
callable from the others. Three of the four row-adding paths already did;
`MI1-I39 — Delivery Note HTY Mode`'s container picker — the HTY flow — did not.

Two guarantees now hold:

  in the browser  every row-adding path recalculates, and `validate` does too
  on the server   calculate_delivery_note_totals fills total_qty when a save
                  arrives with it still 0
"""

import inspect
import json
import re

import frappe
from frappe.tests.utils import FrappeTestCase

# Every enabled Delivery Note Form script that appends rows with add_child.
ROW_ADDING_SCRIPTS = (
	"Delivery Note V2",
	"HTY & VFY",
	"Fetch Batches",
	"MI1-I39 — Delivery Note HTY Mode",
)


def _dn_form_scripts():
	path = frappe.get_app_path("mhr", "fixtures", "client_script.json")
	with open(path, encoding="utf-8") as f:
		records = json.load(f)
	return [
		r
		for r in records
		if r.get("dt") == "Delivery Note" and r.get("view") == "Form" and r.get("enabled")
	]


def _script(name):
	for record in _dn_form_scripts():
		if record.get("name") == name:
			return (record.get("script") or "").replace("\r\n", "\n")
	raise AssertionError(f"{name!r} is missing or disabled in client_script.json")


def _live_code(script):
	"""Drop // comments so a commented-out call cannot pass for a real one."""
	return "\n".join(
		line for line in script.split("\n") if not line.strip().startswith("//")
	)


class TestEveryRowAddingPathRecalculates(FrappeTestCase):
	def test_each_one_calls_calculate_totals(self):
		for name in ROW_ADDING_SCRIPTS:
			with self.subTest(script=name):
				code = _live_code(_script(name))
				self.assertRegex(
					code,
					r"\bcalculate_totals\(",
					f"{name} appends rows but never recalculates — Total "
					"Quantity stays 0 until something else happens to run.",
				)

	def test_the_hty_container_picker_recalculates_before_closing(self):
		"""The path the HTY report came in on."""
		code = _live_code(_script("MI1-I39 — Delivery Note HTY Mode"))
		picker = code.split("frm.refresh_field('items');", 1)[1].split("d.hide();", 1)[0]
		self.assertIn("calculate_totals(frm);", picker)

	def test_validate_recalculates_too(self):
		"""Whichever script added the rows, the browser fixes it before save."""
		code = _live_code(_script("Delivery Note V2"))
		self.assertIn("validate(frm) {\n        calculate_totals(frm);", code)


class TestCalculateTotalsIsDeclaredExactlyOnce(FrappeTestCase):
	"""The scripts share one scope, so a second declaration would silently
	shadow this one depending on `creation asc` order."""

	def test_only_delivery_note_v2_declares_it(self):
		owners = []
		for record in _dn_form_scripts():
			code = _live_code((record.get("script") or "").replace("\r\n", "\n"))
			if re.search(r"^\s*function\s+calculate_totals\s*\(", code, re.M):
				owners.append(record["name"])
		self.assertEqual(owners, ["Delivery Note V2"])

	def test_the_helper_it_writes_through_cannot_throw_on_precision(self):
		"""It runs before every total is written; one exception left both at 0."""
		code = _script("Delivery Note V2")
		self.assertIn("try { precision = frm.precision(fieldname); } catch", code)


class TestServerSideFallback(FrappeTestCase):
	def _source(self):
		return inspect.getsource(frappe.get_attr("mhr.utilis.ensure_total_qty"))

	def test_it_fills_total_qty_when_the_save_arrives_with_zero(self):
		self.assertIn("if not flt(doc.total_qty) and doc.items:", self._source())
		self.assertIn(
			"doc.total_qty = sum(flt(item.qty) for item in doc.items)", self._source()
		)

	def test_the_delivery_note_validate_still_reaches_it(self):
		self.assertIn(
			"ensure_total_qty(doc)",
			inspect.getsource(
				frappe.get_attr("mhr.utilis.calculate_delivery_note_totals")
			),
		)

	def test_it_does_not_overwrite_a_value_erpnext_already_settled(self):
		"""The assignment sits inside the guard, not beside it."""
		lines = self._source().split("\n")
		guard = next(i for i, text in enumerate(lines) if "if not flt(doc.total_qty)" in text)
		assign = next(i for i, text in enumerate(lines) if "doc.total_qty = sum(" in text)

		self.assertEqual(assign, guard + 1)

		def indent(i):
			return len(lines[i]) - len(lines[i].lstrip())

		self.assertGreater(indent(assign), indent(guard))

	def test_the_hook_is_still_registered_on_validate(self):
		handlers = (
			frappe.get_hooks("doc_events", app_name="mhr").get("Delivery Note") or {}
		).get("validate") or []
		self.assertIn("mhr.utilis.calculate_delivery_note_totals", handlers)
