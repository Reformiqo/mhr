"""Sales Order Total Quantity stayed 0 when rows were added by script.

Same fault as the Delivery Note's (test_dn_total_qty), on the other document.
`frm.add_child()` fires no grid event, so neither `items_add` nor ERPNext's
`calculate_taxes_and_totals` runs — and those are what normally set `total_qty`
(erpnext taxes_and_totals.js:361, taxes_and_totals.py:395).

Sales Order's two modes are owned by different files, and neither can call into
the other, so each carries its own fix:

  HTY   mhr/public/js/sales_order_hty.js — four add_child sites, all funnelled
		through so_hty_calculate_totals
  VFY   Client Script "Sales Order Booking" — one add_child site, in
		mi1_so_fetch_batches, which already summed the qty it appended

Both add a `validate` net, and the server fills a blank total on save via
mhr.utilis.ensure_total_qty — the same helper Delivery Note uses.
"""

import inspect
import json

import frappe
from frappe.tests.utils import FrappeTestCase

HTY_JS = "mhr/public/js/sales_order_hty.js"


def _hty_source():
	path = frappe.get_app_path("mhr", "public", "js", "sales_order_hty.js")
	with open(path, encoding="utf-8") as f:
		return f.read()


def _booking_script():
	path = frappe.get_app_path("mhr", "fixtures", "client_script.json")
	with open(path, encoding="utf-8") as f:
		records = json.load(f)
	for record in records:
		if record.get("name") == "Sales Order Booking":
			if not record.get("enabled"):
				raise AssertionError("'Sales Order Booking' is disabled")
			return (record.get("script") or "").replace("\r\n", "\n")
	raise AssertionError("'Sales Order Booking' is missing from client_script.json")


def _live_code(source):
	"""Drop // comments so a commented-out call cannot pass for a real one."""
	return "\n".join(
		line for line in source.split("\n") if not line.strip().startswith("//")
	)


class TestHtyRowAddingPathsRecalculate(FrappeTestCase):
	def setUp(self):
		self.code = _live_code(_hty_source())

	def test_every_add_child_site_reaches_the_totals(self):
		"""One site left out is one flow that saves with Total Quantity 0."""
		appends = self.code.count("add_child('items'")
		self.assertGreater(appends, 0, f"no add_child sites left in {HTY_JS}?")

		# Every append is followed by a recalculation before the next one.
		for chunk in self.code.split("add_child('items'")[1:]:
			self.assertIn(
				"so_hty_calculate_totals(frm)",
				chunk.split("add_child('items'")[0],
				"an items append in sales_order_hty.js never recalculates",
			)

	def test_the_totals_include_total_qty(self):
		body = self.code.split("function so_hty_calculate_totals", 1)[1].split(
			"\nfunction ", 1
		)[0]
		self.assertIn("total_qty += parseFloat(row.qty || 0)", body)
		self.assertIn("so_hty_set_if_changed(frm, 'total_qty', total_qty)", body)

	def test_it_leaves_a_submitted_order_alone(self):
		"""MI1-I106: rewriting totals on a submitted form made it read 'Not Saved'."""
		body = self.code.split("function so_hty_calculate_totals", 1)[1].split(
			"\nfunction ", 1
		)[0]
		self.assertIn("if (frm.doc.docstatus !== 0) return;", body)

	def test_validate_recalculates_too(self):
		self.assertIn(
			"validate: function (frm) {\n        so_hty_calculate_totals(frm);",
			self.code,
		)

	def test_the_write_helper_cannot_throw_on_precision(self):
		"""It gates every total write; one exception would leave them at 0."""
		self.assertIn("precision = frm.precision(fieldname);", _hty_source())
		self.assertRegex(
			_hty_source(),
			r"try \{\s*\n\s*precision = frm\.precision\(fieldname\);\s*\n\s*\} catch",
		)


class TestVfyBookingFlowWritesTheTotal(FrappeTestCase):
	def setUp(self):
		self.code = _live_code(_booking_script())

	def test_the_batch_fetch_writes_the_sum_it_already_computed(self):
		"""mi1_so_fetch_batches clears the table and rebuilds it, accumulating
		total_qty as it goes — it just never put the number on the document."""
		body = self.code.split("function mi1_so_fetch_batches", 1)[1]
		self.assertIn("mi1_so_set_total_qty(frm, total_qty);", body)

	def test_validate_fills_a_blank_total(self):
		body = self.code.split("    validate(frm) {", 1)[1].split("\n    },", 1)[0]
		self.assertIn("if (!mi1_so_is_vfy(frm)) return;", body)
		self.assertIn("mi1_so_set_total_qty(frm, total_qty);", body)

	def test_validate_does_not_overwrite_a_settled_total(self):
		body = self.code.split("    validate(frm) {", 1)[1].split("\n    },", 1)[0]
		self.assertIn("if (flt(frm.doc.total_qty)", body)

	def test_the_write_helper_cannot_throw_on_precision(self):
		self.assertIn(
			"try { precision = frm.precision('total_qty'); } catch", _booking_script()
		)

	def test_the_hty_mode_script_is_still_disabled(self):
		"""MI1-I90 moved HTY into app code. If this record were re-enabled its
		handlers would run beside sales_order_hty.js on the same document."""
		path = frappe.get_app_path("mhr", "fixtures", "client_script.json")
		with open(path, encoding="utf-8") as f:
			records = json.load(f)
		hty = [r for r in records if r.get("name", "").endswith("Sales Order HTY Mode")]
		self.assertTrue(hty)
		for record in hty:
			self.assertFalse(record.get("enabled"), record["name"])


class TestTheFixtureWillActuallySync(FrappeTestCase):
	"""import_file_by_path skips a record whose DB `modified` is not older than
	the JSON's, so an edit without a bump never reaches a migrated site."""

	def test_the_booking_script_modified_was_bumped(self):
		path = frappe.get_app_path("mhr", "fixtures", "client_script.json")
		with open(path, encoding="utf-8") as f:
			records = json.load(f)
		record = next(r for r in records if r.get("name") == "Sales Order Booking")
		self.assertRegex(record["modified"], r"^2026-08-31 ")


class TestServerSideFallback(FrappeTestCase):
	def test_the_hook_is_registered_on_sales_order_validate(self):
		handlers = (
			frappe.get_hooks("doc_events", app_name="mhr").get("Sales Order") or {}
		).get("validate") or []
		self.assertIn("mhr.utilis.ensure_total_qty", handlers)

	def test_it_only_fills_a_blank(self):
		"""The assignment sits inside the guard, not beside it."""
		lines = inspect.getsource(frappe.get_attr("mhr.utilis.ensure_total_qty")).split(
			"\n"
		)
		guard = next(
			i for i, text in enumerate(lines) if "if not flt(doc.total_qty)" in text
		)
		assign = next(
			i for i, text in enumerate(lines) if "doc.total_qty = sum(" in text
		)

		self.assertEqual(assign, guard + 1)

		def indent(i):
			return len(lines[i]) - len(lines[i].lstrip())

		self.assertGreater(indent(assign), indent(guard))

	def test_it_is_not_wired_to_run_before_the_controller(self):
		"""doc_events for `validate` are composed to run after the controller's
		own validate (frappe/model/document.py :: Document.hook), which is where
		calculate_taxes_and_totals sets total_qty. On before_validate it would
		see a blank on every save and get in ERPNext's way."""
		events = frappe.get_hooks("doc_events", app_name="mhr").get("Sales Order") or {}
		self.assertNotIn(
			"mhr.utilis.ensure_total_qty", events.get("before_validate") or []
		)
