"""Get Items From > Sales Order landed no rows on a new Delivery Note.

The server side was fine. `Delivery Note V2`'s refresh handler emptied the
table on EVERY refresh of an unsaved, non-return note:

	if (frm.doc.__islocal && !frm.doc.is_return) {
		frm.clear_table("items");
	}

and erpnext's map_current_doc, which is what "Get Items From" runs, calls
`cur_frm.refresh()` immediately after `frappe.model.sync(r.message)`
(erpnext/public/js/utils.js). So the mapped rows arrived, were synced, and
were wiped before the user ever saw them — no error, no message, an empty
child table. Sales Order > Create > Delivery Note loses its rows the same
way, for the same reason.

The Meher fetch flows never hit it because they append with add_child and
call refresh_field('items'), never a full form refresh.

The clear is kept — a brand-new note should not open holding a stray blank
grid row — but it now runs only when the table holds nothing real. A blank
row has no item_code; a mapped or hand-entered row always does.
"""

import inspect
import json
import re

import frappe
from frappe.tests.utils import FrappeTestCase

SCRIPT_NAME = "Delivery Note V2"


def _script(name=SCRIPT_NAME):
	path = frappe.get_app_path("mhr", "fixtures", "client_script.json")
	with open(path, encoding="utf-8") as f:
		records = json.load(f)
	for record in records:
		if record.get("name") == name:
			if not record.get("enabled"):
				raise AssertionError(f"{name!r} is disabled")
			return (record.get("script") or "").replace("\r\n", "\n")
	raise AssertionError(f"{name!r} is missing from client_script.json")


def _live_code(source):
	"""Drop // comments so a commented-out line cannot pass for real code."""
	return "\n".join(
		line for line in source.split("\n") if not line.strip().startswith("//")
	)


def _refresh_body():
	code = _live_code(_script())
	return code.split("refresh(frm) {", 1)[1].split("\n    },", 1)[0]


class TestARefreshNoLongerDiscardsMappedRows(FrappeTestCase):
	def test_the_clear_is_gated_on_the_table_holding_nothing_real(self):
		body = _refresh_body()
		self.assertIn("mi1_has_real_rows", body)
		self.assertIn("return row.item_code;", body)
		self.assertIn("if (!mi1_has_real_rows) {", body)

	def test_the_gate_wraps_the_clear_rather_than_sitting_beside_it(self):
		"""An unguarded clear_table anywhere in refresh reopens the bug."""
		body = _refresh_body()
		lines = body.split("\n")
		gate = next(i for i, text in enumerate(lines) if "if (!mi1_has_real_rows)" in text)
		clear = next(i for i, text in enumerate(lines) if 'clear_table("items")' in text)

		self.assertGreater(clear, gate)

		def indent(i):
			return len(lines[i]) - len(lines[i].lstrip())

		self.assertGreater(indent(clear), indent(gate))

	def test_refresh_clears_exactly_once(self):
		self.assertEqual(_refresh_body().count("clear_table"), 1)

	def test_it_still_only_applies_to_an_unsaved_non_return_note(self):
		"""Existing behaviour: a saved note and a return were never cleared."""
		self.assertIn(
			"if (frm.doc.__islocal && !frm.doc.is_return) {", _refresh_body()
		)

	def test_the_clear_never_looks_at_the_mode(self):
		"""Get Items From must behave identically on HTY and VFY. The clear is
		the only thing that ever discarded mapped rows, so it has to stay
		mode-blind — a transaction_type branch here would fix one and not the
		other."""
		body = _refresh_body()
		start = body.index("if (frm.doc.__islocal")
		end = body.index("clear_table")
		self.assertNotIn("transaction_type", body[start:end])


class TestTheOtherRowAddingPathsAreUnaffected(FrappeTestCase):
	"""They append with add_child + refresh_field, never a full frm.refresh(),
	which is why the clear never reached them and why nothing here changes."""

	def _dn_form_scripts(self):
		path = frappe.get_app_path("mhr", "fixtures", "client_script.json")
		with open(path, encoding="utf-8") as f:
			records = json.load(f)
		return [
			r
			for r in records
			if r.get("dt") == "Delivery Note" and r.get("view") == "Form" and r.get("enabled")
		]

	def test_no_delivery_note_script_calls_a_full_form_refresh(self):
		for record in self._dn_form_scripts():
			code = _live_code((record.get("script") or "").replace("\r\n", "\n"))
			with self.subTest(script=record["name"]):
				self.assertIsNone(
					re.search(r"\bfrm\.refresh\(\)", code),
					f"{record['name']} calls frm.refresh(), which re-runs every "
					"refresh handler on the document",
				)

	def test_the_hty_container_picker_still_clears_before_it_refills(self):
		"""That clear is deliberate and immediately followed by its own rows."""
		code = _live_code(_script("MI1-I39 — Delivery Note HTY Mode"))
		after = code.split("frm.clear_table('items');", 1)[1]
		self.assertIn("frm.add_child('items'", after)

	def test_no_other_script_has_started_clearing_the_table(self):
		"""Two scripts clear, and each is accounted for above: Delivery Note V2
		behind the new guard, and the HTY picker which refills immediately. A
		third would be a new way to lose mapped rows."""
		clearing = sorted(
			record["name"]
			for record in self._dn_form_scripts()
			if "clear_table" in _live_code((record.get("script") or "").replace("\r\n", "\n"))
		)
		self.assertEqual(
			clearing, ["Delivery Note V2", "MI1-I39 — Delivery Note HTY Mode"]
		)

	def test_the_hty_mode_refresh_handler_does_not_touch_rows(self):
		"""It runs on the same refresh that follows the mapping, so if it
		rebuilt or emptied the grid the HTY path would break where VFY does
		not. It only toggles field and column visibility."""
		code = _live_code(_script("MI1-I39 — Delivery Note HTY Mode"))
		body = code.split("function mi1_i39_apply_dn_hty(frm) {", 1)[1].split("\n}", 1)[0]
		for forbidden in ("clear_table", "add_child", "frm.set_value"):
			self.assertNotIn(forbidden, body)


class TestTheServerSideMappingIsUntouched(FrappeTestCase):
	"""MI1-I108: the override's signature must keep matching upstream's, or
	map_docs — which is what Get Items From calls — raises a TypeError before
	any of the above matters."""

	def test_the_override_is_still_wired(self):
		overrides = frappe.get_hooks("override_whitelisted_methods", app_name="mhr")
		self.assertIn(
			"mhr.sales_order_to_delivery_note.make_delivery_note",
			overrides.get(
				"erpnext.selling.doctype.sales_order.sales_order.make_delivery_note"
			)
			or [],
		)

	def test_it_takes_the_three_positionals_map_docs_passes(self):
		method = frappe.get_attr("mhr.sales_order_to_delivery_note.make_delivery_note")
		parameters = list(inspect.signature(method).parameters.values())
		self.assertEqual(
			[p.name for p in parameters], ["source_name", "target_doc", "kwargs"]
		)
		for parameter in parameters:
			self.assertEqual(parameter.kind, inspect.Parameter.POSITIONAL_OR_KEYWORD)

	def _carry_source(self):
		return inspect.getsource(
			frappe.get_attr("mhr.sales_order_to_delivery_note.carry_sales_order_details")
		)

	def test_it_never_drops_a_row_it_was_given(self):
		"""Nothing in this app removes mapped rows in either mode — the only
		thing that ever did was the client-side clear above."""
		source = self._carry_source()
		self.assertNotIn("target.items = ", source)
		self.assertNotIn(".remove(", source)
