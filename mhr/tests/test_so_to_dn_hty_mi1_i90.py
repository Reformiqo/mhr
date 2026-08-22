"""MI1-I90 — Sales Order -> Delivery Note carry-over for HTY.

The flow under test: raise an HTY Sales Order, press Create > Delivery Note,
and get a Delivery Note that already carries the container / lot / batch and
the ordered quantity.

These tests are deliberately weighted towards the two things that fail
SILENTLY — a mapped-but-empty item row, and a qty quietly replaced by the
batch's own — plus the guarantees the ticket spells out: the Delivery Note's
own series, its existing validations, and the direct-creation path all stay
as they are.
"""

import inspect

import frappe
from frappe.tests.utils import FrappeTestCase

from mhr import sales_order_to_delivery_note as so2dn


def _row(**kwargs):
	"""A Delivery Note Item-shaped stand-in.

	carry_hty_details() only ever does .get() / .set() / attribute writes on
	the rows, so a plain _dict exercises the real code path without needing a
	saved Sales Order and its stock on the test site.
	"""
	return frappe._dict(kwargs)


class TestItemFieldMap(FrappeTestCase):
	"""The three pairs frappe's mapper cannot see."""

	def test_maps_the_three_mismatched_pairs(self):
		self.assertEqual(
			so2dn.ITEM_FIELD_MAP,
			{
				"custom_container_number": "custom_container_no",
				"custom_lot_number": "custom_lot_no",
				"custom_batch_no": "batch_no",
			},
		)

	def test_source_fields_exist_on_sales_order_item(self):
		for fieldname in so2dn.ITEM_FIELD_MAP:
			with self.subTest(fieldname=fieldname):
				self.assertTrue(
					frappe.db.exists(
						"Custom Field", {"dt": "Sales Order Item", "fieldname": fieldname}
					),
					f"Sales Order Item.{fieldname} is gone — the map now copies from "
					"a field that does not exist, so the Delivery Note row lands empty.",
				)

	def test_target_fields_exist_on_delivery_note_item(self):
		meta = frappe.get_meta("Delivery Note Item")
		for target in so2dn.ITEM_FIELD_MAP.values():
			with self.subTest(fieldname=target):
				self.assertIsNotNone(
					meta.get_field(target),
					f"Delivery Note Item.{target} is gone.",
				)

	def test_pairs_really_do_differ(self):
		"""If a pair ever becomes same-named, the mapper handles it and the
		entry here is dead weight — worth knowing rather than leaving."""
		for src, target in so2dn.ITEM_FIELD_MAP.items():
			with self.subTest(pair=(src, target)):
				self.assertNotEqual(src, target)


class TestNamingSeriesIsNotCarried(FrappeTestCase):
	"""Requirement: the Delivery Challan series continues unchanged, whether
	the Challan came from a Sales Order or was made directly.

	frappe's mapper skips any field with no_copy on EITHER side, so this holds
	by construction — but only while both flags stay set. `HTY-SO-.YYYY.-` is
	not among Delivery Note's series options, so a regression here is a hard
	save error for every user pressing the button."""

	def test_naming_series_is_no_copy_on_both_doctypes(self):
		for doctype in ("Sales Order", "Delivery Note"):
			with self.subTest(doctype=doctype):
				self.assertEqual(
					frappe.get_meta(doctype).get_field("naming_series").no_copy,
					1,
					f"{doctype}.naming_series lost no_copy — the Sales Order series "
					"will now be copied onto the Delivery Note.",
				)

	def test_wrapper_never_writes_naming_series(self):
		src = inspect.getsource(so2dn)
		self.assertNotIn("naming_series =", src)
		self.assertNotIn('set("naming_series"', src)


class TestCarryOverIsHTYOnly(FrappeTestCase):
	"""VFY must be byte-for-byte what it was before this module existed."""

	def test_vfy_source_returns_target_untouched(self):
		target = frappe._dict(items=[_row(so_detail="X", custom_cone=4, batch_no="B")])

		so = frappe.get_all(
			"Sales Order",
			filters={"transaction_type": ("!=", "HTY")},
			pluck="name",
			limit=1,
		)
		if not so:
			self.skipTest("No non-HTY Sales Order on this site.")

		out = so2dn.carry_hty_details(so[0], target)

		self.assertIsNone(out.get("transaction_type"))
		self.assertIsNone(out["items"][0].get("custom_qty_manual_edit"))

	def test_empty_target_short_circuits(self):
		"""Must not even load the Sales Order — nothing to write to."""
		self.assertIsNone(so2dn.carry_hty_details("does-not-exist", None))
		self.assertEqual(
			so2dn.carry_hty_details("does-not-exist", frappe._dict(items=[])).get("items"),
			[],
		)


class TestOrderedQtyProtection(FrappeTestCase):
	"""Gap 2: 'Cone Qty Calcuation'.before_save recomputes
	qty = (Batch.batch_qty * cone) / cone_copy for any row with a batch AND a
	cone, and honours custom_qty_manual_edit as the opt-out."""

	def test_row_with_batch_and_cone_is_flagged(self):
		row = _row(batch_no="BATCH-1", custom_cone=6, qty=10)
		so2dn._protect_ordered_qty(row)
		self.assertEqual(row.custom_qty_manual_edit, 1)
		self.assertEqual(row.qty, 10, "The ordered qty must not be touched here.")

	def test_cone_copy_is_seeded_from_cone(self):
		"""Left empty, the Client Script sets it itself and the ratio becomes
		1 — i.e. qty := the batch's full quantity."""
		row = _row(batch_no="BATCH-1", custom_cone=6)
		so2dn._protect_ordered_qty(row)
		self.assertEqual(row.custom_cone_copy, 6)

	def test_existing_cone_copy_is_preserved(self):
		row = _row(batch_no="BATCH-1", custom_cone=6, custom_cone_copy=4)
		so2dn._protect_ordered_qty(row)
		self.assertEqual(row.custom_cone_copy, 4)

	def test_row_without_a_batch_is_left_alone(self):
		"""The script skips these, so flagging them would suppress a
		recalculation the user is entitled to."""
		row = _row(custom_cone=6)
		so2dn._protect_ordered_qty(row)
		self.assertIsNone(row.get("custom_qty_manual_edit"))

	def test_row_without_a_cone_is_left_alone(self):
		row = _row(batch_no="BATCH-1")
		so2dn._protect_ordered_qty(row)
		self.assertIsNone(row.get("custom_qty_manual_edit"))

	def test_the_optout_flag_still_exists_on_delivery_note_item(self):
		self.assertIsNotNone(
			frappe.get_meta("Delivery Note Item").get_field("custom_qty_manual_edit"),
			"The opt-out this module relies on is gone; mapped rows would have "
			"their ordered qty overwritten by the batch qty.",
		)

	def test_client_script_still_honours_the_optout(self):
		"""Pins the contract in the other direction: if someone drops the guard
		from 'Cone Qty Calcuation'.before_save, the flag stops protecting
		anything and this module is silently useless."""
		script = frappe.db.get_value("Client Script", "Cone Qty Calcuation", "script") or ""
		if not script:
			self.skipTest("Cone Qty Calcuation not present on this site.")

		idx = script.find("before_save(frm) {")
		self.assertGreater(idx, -1, "before_save handler missing.")
		self.assertIn(
			"if (row.custom_qty_manual_edit) return;",
			script[idx : idx + 800],
			"before_save no longer skips manually-edited rows.",
		)


class TestFetchControlsAreNotCarried(FrappeTestCase):
	"""fetch_batches arriving ticked would append a bulk fetch on top of the
	rows just mapped from the Sales Order."""

	def test_fetch_batches_is_cleared(self):
		src = inspect.getsource(so2dn.carry_hty_details)
		self.assertIn("target.fetch_batches = 0", src)

	def test_sales_order_names_its_fetch_controls_differently(self):
		"""Belt and braces: different fieldnames mean the mapper cannot copy
		them even if the explicit clear were removed."""
		meta = frappe.get_meta("Sales Order")
		for fieldname in ("count", "fetch_batches"):
			with self.subTest(fieldname=fieldname):
				self.assertIsNone(
					meta.get_field(fieldname),
					f"Sales Order now has a field named {fieldname!r}, which the "
					"mapper will copy straight onto the Delivery Note.",
				)


class TestDirectDeliveryNoteFlowUntouched(FrappeTestCase):
	"""Requirement: users who do not want a Sales Order keep creating the
	Delivery Note directly, exactly as today."""

	def test_no_server_hook_is_added_to_delivery_note(self):
		from mhr import hooks

		dn_events = hooks.doc_events.get("Delivery Note", {})
		flat = []
		for handlers in dn_events.values():
			flat.extend(handlers if isinstance(handlers, list) else [handlers])

		for handler in flat:
			with self.subTest(handler=handler):
				self.assertNotIn(
					"sales_order_to_delivery_note",
					handler,
					"This module must not run on Delivery Notes created directly.",
				)

	def test_only_the_sales_order_entry_point_is_overridden(self):
		from mhr import hooks

		self.assertEqual(
			list(hooks.override_whitelisted_methods),
			["erpnext.selling.doctype.sales_order.sales_order.make_delivery_note"],
		)

	def test_override_target_is_importable(self):
		"""A typo here is a 500 on the button, for VFY users too."""
		from mhr import hooks

		target = hooks.override_whitelisted_methods[
			"erpnext.selling.doctype.sales_order.sales_order.make_delivery_note"
		]
		self.assertTrue(callable(frappe.get_attr(target)))


class TestContainerNotesOnSalesOrder(FrappeTestCase):
	"""MI1-I90: Sales Order runs the same Container-notes fetch Delivery Note
	runs (MI1-I83, extended to HTY by MI1-I101), reusing the function verbatim
	rather than forking it."""

	def test_hook_is_registered_on_sales_order(self):
		handlers = (frappe.get_hooks("doc_events", app_name="mhr").get("Sales Order") or {}).get(
			"validate"
		) or []
		self.assertIn("mhr.utilis.fetch_notes_from_container", handlers)

	def test_delivery_note_still_runs_it_too(self):
		"""Sharing the function must not move it off Delivery Note."""
		handlers = (frappe.get_hooks("doc_events", app_name="mhr").get("Delivery Note") or {}).get(
			"validate"
		) or []
		self.assertIn("mhr.utilis.fetch_notes_from_container", handlers)

	def test_sales_order_has_the_fields_the_function_reads(self):
		meta = frappe.get_meta("Sales Order")
		for fieldname in ("transaction_type", "custom_container_no", "custom_notes"):
			with self.subTest(fieldname=fieldname):
				self.assertIsNotNone(
					meta.get_field(fieldname),
					f"Sales Order.{fieldname} missing — the shared notes hook would "
					"read None and silently do nothing.",
				)

	def test_existing_notes_are_never_overwritten(self):
		from mhr.utilis import fetch_notes_from_container

		doc = frappe._dict(
			transaction_type="HTY",
			custom_container_no="ANY",
			custom_notes="typed by the user",
		)
		fetch_notes_from_container(doc)
		self.assertEqual(doc.custom_notes, "typed by the user")

	def test_other_transaction_types_are_ignored(self):
		from mhr.utilis import fetch_notes_from_container

		doc = frappe._dict(transaction_type="SOMETHING", custom_container_no="ANY")
		fetch_notes_from_container(doc)
		self.assertIsNone(doc.get("custom_notes"))


class TestConeToQtyOnSalesOrder(FrappeTestCase):
	"""The Delivery Note's 'Cone Qty Calcuation' rule, ported to Sales Order:

	    qty = (Batch.batch_qty * custom_cone) / custom_cone_copy

	Both fields use the Delivery Note Item fieldnames so Create > Delivery Note
	carries them across and neither form re-derives a qty the other settled."""

	FIELDS = ("custom_cone_copy", "custom_qty_manual_edit")

	def test_fields_exist_on_sales_order_item(self):
		meta = frappe.get_meta("Sales Order Item")
		for fieldname in self.FIELDS:
			with self.subTest(fieldname=fieldname):
				self.assertIsNotNone(meta.get_field(fieldname))

	def test_fieldnames_match_delivery_note_item(self):
		"""Different names here would mean frappe's mapper drops them, and the
		Delivery Note would recompute a qty the Sales Order already fixed."""
		dn_meta = frappe.get_meta("Delivery Note Item")
		for fieldname in self.FIELDS:
			with self.subTest(fieldname=fieldname):
				self.assertIsNotNone(
					dn_meta.get_field(fieldname),
					f"Delivery Note Item.{fieldname} missing — the pair is no longer "
					"symmetric.",
				)

	def test_fields_are_copyable(self):
		for doctype in ("Sales Order Item", "Delivery Note Item"):
			for fieldname in self.FIELDS:
				with self.subTest(doctype=doctype, fieldname=fieldname):
					field = frappe.get_meta(doctype).get_field(fieldname)
					if field is None:
						continue
					self.assertFalse(
						field.no_copy,
						f"{doctype}.{fieldname} is no_copy — the mapper will drop it.",
					)

	def test_handlers_are_hty_gated(self):
		"""VFY Sales Order qty belongs to the 'Sales Order Booking' Client
		Script and must not be touched by this file."""
		path = frappe.get_app_path("mhr", "public", "js", "sales_order_hty.js")
		with open(path, encoding="utf-8") as f:
			source = f.read()

		idx = source.find("function so_hty_recalc_row_qty(")
		self.assertGreater(idx, -1, "so_hty_recalc_row_qty missing.")
		self.assertIn("if (!so_hty_is_hty(frm)) return;", source[idx : idx + 200])

		idx = source.find("frappe.ui.form.on('Sales Order Item', {")
		self.assertGreater(idx, -1, "Sales Order Item handlers missing.")
		block = source[idx:]
		self.assertEqual(
			block.count("if (!so_hty_is_hty(frm)) return;"), 2,
			"Both custom_cone and qty must return early on a VFY Sales Order. "
			"(so_hty_recalc_row_qty carries the third gate and is defined above "
			"this block.)",
		)

	def test_new_rows_are_seeded_with_a_cone_copy(self):
		"""Without this the first cone edit divides by an empty cone_copy and
		the row jumps to the batch's full quantity."""
		path = frappe.get_app_path("mhr", "public", "js", "sales_order_hty.js")
		with open(path, encoding="utf-8") as f:
			source = f.read()

		idx = source.find("function so_hty_apply_selected_batches(")
		self.assertGreater(idx, -1)
		self.assertIn("custom_cone_copy: data.custom_cone,", source[idx : idx + 3000])
