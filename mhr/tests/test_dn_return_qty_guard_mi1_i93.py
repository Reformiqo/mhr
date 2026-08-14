"""MI1-I93 — Delivery Note returns must keep negative qty.

A return DN showed negative qty in the grid, then flipped positive on submit
and ERPNext rejected it with "Stock Qty must be negative in return document".

Cause: the 'Cone Qty Calcuation' Client Script recomputes

    new_qty = (Batch.batch_qty * custom_cone) / custom_cone_copy

and Batch.batch_qty is always POSITIVE, so the recomputed value overwrote the
negative qty. `before_save` is what made it bite at submit time.

These tests pin the guards in the Client Script bodies. Behaviour is exercised
manually on the form — there is no server-side hook to assert against, because
the defect was never server-side (see test_no_server_hook_writes_item_qty).
"""

import frappe
from frappe.tests.utils import FrappeTestCase

GUARD = "mi1_i93_is_return"
COMMIT_TAG = "MI1-I93"


def _script(name):
	return frappe.db.get_value("Client Script", name, "script") or ""


class TestConeQtyCalcuationReturnGuard(FrappeTestCase):
	"""The script that actually caused the bug."""

	NAME = "Cone Qty Calcuation"

	def setUp(self):
		self.src = _script(self.NAME)
		if not self.src:
			self.skipTest(f"{self.NAME} not present on this site.")

	def test_script_is_still_enabled(self):
		"""The fix is a guard, not a disable — non-return DNs still need it."""
		self.assertEqual(
			frappe.db.get_value("Client Script", self.NAME, "enabled"), 1,
			f"{self.NAME} must stay enabled; MI1-I93 guards it, it does not disable it.",
		)

	def test_helper_is_defined(self):
		self.assertIn(
			f"function {GUARD}(", self.src,
			"The is_return helper must be defined in the script.",
		)

	def test_helper_falls_back_to_normal_behaviour(self):
		"""If neither frm nor cur_frm resolves, the script must behave as before —
		a return must never be *assumed*."""
		self.assertIn("cur_frm", self.src)
		self.assertIn("return !!(d && d.is_return);", self.src)

	def test_before_save_is_guarded(self):
		"""before_save is the one that fires at submit and flipped the sign."""
		idx = self.src.find("before_save(frm) {")
		self.assertGreater(idx, -1, "before_save handler missing.")
		window = self.src[idx: idx + 200]
		self.assertIn(GUARD, window, "before_save must return early on a return document.")

	def test_both_qty_writers_are_guarded(self):
		for fn in ("calculate_qty_from_database", "fetch_batch_qty_and_calculate"):
			with self.subTest(function=fn):
				idx = self.src.find(f"function {fn}(cdn, row) {{")
				self.assertGreater(idx, -1, f"{fn} missing.")
				window = self.src[idx: idx + 200]
				self.assertIn(
					GUARD, window,
					f"{fn} writes qty from Batch.batch_qty and must be inert on returns.",
				)

	def test_handler_binder_is_guarded(self):
		idx = self.src.find("function bind_cone_handlers(frm) {")
		self.assertGreater(idx, -1, "bind_cone_handlers missing.")
		self.assertIn(GUARD, self.src[idx: idx + 200])

	def test_qty_is_still_written_for_non_returns(self):
		"""Guard only — the recalculation itself must remain intact."""
		self.assertIn("let new_qty = (database_qty * cone) / cone_copy;", self.src)
		self.assertEqual(
			self.src.count("let new_qty = (database_qty * cone) / cone_copy;"), 2,
			"Both recalculation sites must still exist for non-return DNs.",
		)


class TestRowAddingScriptsReturnGuard(FrappeTestCase):
	"""The fetch / popup / scan flows append rows carrying Batch.batch_qty,
	which is positive. On a return that reproduces the same sign error."""

	CASES = {
		"Fetch Batches": ["fetch_batches: function(frm) {"],
		"HTY & VFY": ["async custom_container_no(frm) {", "async custom_denier(frm) {"],
		"Delivery Note V2": [
			"custom_supplier_batch_no: function(frm) {",
			"custom_scan_batch_no: function(frm, cdt, cdn) {",
		],
	}

	def test_entry_points_are_guarded(self):
		for name, anchors in self.CASES.items():
			src = _script(name)
			if not src:
				self.skipTest(f"{name} not present on this site.")
			for anchor in anchors:
				with self.subTest(script=name, handler=anchor):
					# Find the first LIVE (non-commented) occurrence — these
					# scripts carry large commented-out predecessors.
					live = None
					for line_no, line in enumerate(src.replace("\r\n", "\n").split("\n")):
						if anchor in line and not line.lstrip().startswith("//"):
							live = line_no
							break
					self.assertIsNotNone(live, f"{anchor} not found live in {name}.")
					body = "\n".join(
						src.replace("\r\n", "\n").split("\n")[live: live + 3]
					)
					self.assertIn(
						"frm.doc.is_return", body,
						f"{name} -> {anchor} must return early on a return document.",
					)


class TestServerSideIsNotInvolved(FrappeTestCase):
	"""Rules out the server hooks, so the diagnosis stays pinned."""

	def test_no_server_hook_writes_item_qty(self):
		import inspect

		from mhr.utilis import (
			calculate_delivery_note_totals,
			set_return_cone_from_original,
		)

		for fn in (calculate_delivery_note_totals, set_return_cone_from_original):
			with self.subTest(function=fn.__name__):
				src = inspect.getsource(fn)
				self.assertNotIn(
					"item.qty =", src,
					f"{fn.__name__} must not write item qty — that would be a second "
					"source of the return sign bug.",
				)

	def test_no_server_script_targets_delivery_note_qty(self):
		for name in frappe.get_all(
			"Server Script",
			filters={"reference_doctype": "Delivery Note", "disabled": 0},
			pluck="name",
		):
			with self.subTest(server_script=name):
				body = frappe.db.get_value("Server Script", name, "script") or ""
				self.assertNotIn(
					"item.qty =", body,
					f"Server Script {name} writes item qty on Delivery Note.",
				)
