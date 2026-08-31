"""MI1-I103 — Send to Subcontractor: warehouse, ordering, and re-fetching.

1. Submitting the entry rewrote Container.set_warehouse ("Accepted Warehouse")
   and three free-text location notes with the entry's target warehouse. On
   MCJC-2222-1 that turned Finished Goods - MC into SURAMYA YARN - MC while the
   inward Purchase Receipt still read Finished Goods - MC; 87 submitted
   Containers carry the fingerprint. Both hooks are gone.

2. `mhr.note.fetch_batches` had no order_by, so MAT-GD-2026-00008 landed
   6876, 6870, 6872, 6879, 6874 — and Fetch Batches left Supplier Batch No
   blank until save, hiding the order entirely.

3. The availability clamp counted a batch's Serial and Batch Bundle balance in
   ANY warehouse, so a batch already sent to the subcontractor still looked
   available. It now takes a warehouse, and the form refuses to fetch without
   one.

The Delivery Note flow passes no warehouse and is unchanged.
"""

import inspect
import json
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from mhr import note

SE_SCRIPT = "Stock Entry Container Info"

# The newest value this Client Script has already shipped with.
# import_file_by_path skips a non-DocType record whose DB `modified` is not
# older than the JSON's, so raise this whenever the script is edited again.
SE_SCRIPT_MODIFIED_BEFORE_THE_FIX = "2026-08-25 16:30:00.000000"

# The order MAT-GD-2026-00008 actually produced, and what it should have been.
REPORTED_ORDER = ["6876", "6870", "6872", "6879", "6874"]
EXPECTED_ORDER = ["6870", "6872", "6874", "6876", "6879"]


def _client_script(name, dt):
	path = frappe.get_app_path("mhr", "fixtures", "client_script.json")
	with open(path, encoding="utf-8") as f:
		records = json.load(f)
	for record in records:
		if record.get("name") == name and record.get("dt") == dt:
			return record
	raise AssertionError(f"{name!r} ({dt}) is missing from client_script.json")


class TestWarehouseSyncHooksAreGone(FrappeTestCase):
	"""Issue 1: a stock transfer must not rewrite inward attributes."""

	def _stock_entry_events(self):
		from mhr import hooks

		return hooks.doc_events["Stock Entry"]

	def test_on_submit_no_longer_syncs_warehouse(self):
		self.assertNotIn(
			"mhr.utilis.update_batch_warehouse_on_stock_entry",
			self._stock_entry_events()["on_submit"],
		)

	def test_on_cancel_no_longer_syncs_warehouse(self):
		self.assertNotIn(
			"mhr.utilis.revert_batch_warehouse_on_stock_entry",
			self._stock_entry_events()["on_cancel"],
		)

	def test_the_functions_are_gone_not_merely_unhooked(self):
		"""Left in place they are one hooks.py edit away from returning."""
		from mhr import utilis

		for fname in (
			"update_batch_warehouse_on_stock_entry",
			"revert_batch_warehouse_on_stock_entry",
			"_sync_batch_warehouse",
		):
			with self.subTest(function=fname):
				self.assertFalse(
					hasattr(utilis, fname),
					f"mhr.utilis.{fname} is back; it overwrites "
					"Container.set_warehouse on every Stock Entry submit.",
				)

	def test_the_subcontract_receipt_hooks_survived(self):
		"""MI1-I50's handlers shared those lists."""
		events = self._stock_entry_events()
		self.assertIn("mhr.utilis.apply_subcontract_receipt", events["on_submit"])
		self.assertIn("mhr.utilis.revert_subcontract_receipt", events["on_cancel"])
		self.assertIn("mhr.utilis.create_receive_batches", events["before_submit"])

	def test_nothing_in_utilis_writes_the_accepted_warehouse(self):
		"""It drives the Purchase Receipt and the report column of that name."""
		from mhr import utilis

		source = inspect.getsource(utilis)
		code = "\n".join(
			line for line in source.split("\n") if not line.lstrip().startswith("#")
		)
		self.assertNotIn('"set_warehouse"', code)
		self.assertNotIn("'set_warehouse'", code)


class TestSupplierBatchOrdering(FrappeTestCase):
	"""Issue 2."""

	def test_the_reported_sequence_sorts_ascending(self):
		rows = [{"custom_supplier_batch_no": v} for v in REPORTED_ORDER]
		rows.sort(key=note.supplier_batch_sort_key)
		self.assertEqual([r["custom_supplier_batch_no"] for r in rows], EXPECTED_ORDER)

	def test_it_sorts_numerically_not_as_text(self):
		"""custom_supplier_batch_no is a Data column, so SQL alone puts '10'
		before '9'."""
		rows = [{"custom_supplier_batch_no": v} for v in ["10", "9", "100", "1"]]
		rows.sort(key=note.supplier_batch_sort_key)
		self.assertEqual(
			[r["custom_supplier_batch_no"] for r in rows], ["1", "9", "10", "100"]
		)

	def test_blanks_and_non_numerics_sort_last_without_raising(self):
		rows = [{"custom_supplier_batch_no": v} for v in ["7", None, "A-2", "", "3"]]
		rows.sort(key=note.supplier_batch_sort_key)
		self.assertEqual(
			[r["custom_supplier_batch_no"] for r in rows[:2]], ["3", "7"]
		)
		self.assertEqual(len(rows), 5)

	def test_the_query_orders_too(self):
		"""The SQL order decides which rows survive the scan limit."""
		source = inspect.getsource(note.fetch_batches)
		self.assertIn('order_by="custom_supplier_batch_no asc, name asc"', source)


class TestFetchBatchesScanAndTrim(FrappeTestCase):
	"""The limit used to be applied before the availability check."""

	def _rows(self, supplier_batch_nos):
		return [
			{
				"name": f"MCJC-2222130220266{v}",
				"custom_supplier_batch_no": v,
				"batch_qty": 25.5,
			}
			for v in supplier_batch_nos
		]

	def test_returns_the_lowest_n_ascending(self):
		rows = self._rows(REPORTED_ORDER + ["6881"])
		with patch.object(frappe, "get_all", return_value=rows), patch.object(
			note, "_clamp_batch_qty_to_available", lambda *a, **k: None
		):
			out = note.fetch_batches(limit=5, container_no="MCJC-2222")
		self.assertEqual(
			[b["custom_supplier_batch_no"] for b in out], EXPECTED_ORDER
		)

	def test_it_over_scans_so_consumed_rows_do_not_reduce_the_count(self):
		with patch.object(frappe, "get_all", return_value=[]) as get_all:
			note.fetch_batches(limit=5, container_no="MCJC-2222")
		self.assertEqual(
			get_all.call_args.kwargs["limit"], 5 * note.SCAN_MULTIPLIER
		)

	def test_the_scan_is_bounded_when_no_limit_is_given(self):
		with patch.object(frappe, "get_all", return_value=[]) as get_all:
			note.fetch_batches(limit=0, container_no="MCJC-2222")
		self.assertEqual(get_all.call_args.kwargs["limit"], note.MAX_SCAN)


class TestAvailabilityIsWarehouseScoped(FrappeTestCase):
	"""Issue 3."""

	def test_a_warehouse_narrows_the_balance_query(self):
		batches = [{"name": "B1", "batch_qty": 25.9}]
		with patch.object(frappe.db, "sql", return_value=[]) as sql:
			note._clamp_batch_qty_to_available(batches, False, "Finished Goods - MC")
		query, params = sql.call_args[0][0], sql.call_args[0][1]
		self.assertIn("sbb.warehouse = %s", query)
		self.assertEqual(params, ("B1", "Finished Goods - MC"))

	def test_a_batch_with_no_balance_there_is_zeroed(self):
		"""Which is what makes fetch_batches drop it."""
		batches = [{"name": "B1", "batch_qty": 25.9}]
		with patch.object(frappe.db, "sql", return_value=[]):
			note._clamp_batch_qty_to_available(batches, False, "Finished Goods - MC")
		self.assertEqual(batches[0]["batch_qty"], 0)

	def test_omitting_the_warehouse_keeps_the_old_any_warehouse_query(self):
		"""The Delivery Note flow does not pass one."""
		batches = [{"name": "B1", "batch_qty": 25.9}]
		with patch.object(frappe.db, "sql", return_value=[]) as sql:
			note._clamp_batch_qty_to_available(batches, False)
		query, params = sql.call_args[0][0], sql.call_args[0][1]
		self.assertNotIn("sbb.warehouse = %s", query)
		self.assertEqual(params, ("B1",))

	def test_the_parameter_is_optional(self):
		signature = inspect.signature(note.fetch_batches)
		self.assertIn("warehouse", signature.parameters)
		self.assertIsNone(signature.parameters["warehouse"].default)


class TestStockEntryClientPassesTheWarehouse(FrappeTestCase):
	def setUp(self):
		self.script = _client_script(SE_SCRIPT, "Stock Entry")["script"]

	def test_fetch_batches_receives_it(self):
		self.assertIn("warehouse: source_warehouse,", self.script)

	def test_the_resolver_prefers_the_entry_then_a_row_then_the_container(self):
		for fragment in (
			"function get_source_warehouse_se(frm, callback) {",
			"if (frm.doc.from_warehouse) { callback(frm.doc.from_warehouse); return; }",
			"return row.s_warehouse;",
			"'Container', frm.doc.custom_container_number, 'set_warehouse'",
		):
			with self.subTest(fragment=fragment):
				self.assertIn(fragment, self.script)

	def test_the_container_number_lookup_is_unchanged(self):
		"""get_container_no still backs the other three call sites."""
		self.assertIn("function get_container_no(frm, callback) {", self.script)
		self.assertIn("get_container_no(frm, function(container_no) {", self.script)

	def test_modified_moved_forward(self):
		from frappe.utils import get_datetime

		record = _client_script(SE_SCRIPT, "Stock Entry")
		self.assertGreater(
			get_datetime(record["modified"]),
			get_datetime(SE_SCRIPT_MODIFIED_BEFORE_THE_FIX),
			"import_file_by_path skips a fixture record whose DB timestamp is "
			"not older than the JSON's — this edit would never reach the site.",
		)

	def test_it_is_still_enabled(self):
		self.assertEqual(_client_script(SE_SCRIPT, "Stock Entry")["enabled"], 1)


class TestDeliveryNoteFetchIsUntouched(FrappeTestCase):
	"""'Existing functionality should not be affected' — the Delivery Note's
	own Fetch Batches must keep its any-warehouse behaviour."""

	def test_the_dn_script_passes_no_warehouse(self):
		script = _client_script("Fetch Batches", "Delivery Note")["script"]
		self.assertIn("mhr.note.fetch_batches", script)
		self.assertNotIn("warehouse:", script)


class TestHealPatchIsRegistered(FrappeTestCase):
	def test_it_is_in_patches_txt(self):
		path = frappe.get_app_path("mhr", "patches.txt")
		with open(path, encoding="utf-8") as f:
			lines = [line.strip() for line in f]
		self.assertTrue(
			any(
				line.startswith("mhr.patches.v1_0.heal_container_accepted_warehouse")
				for line in lines
			),
			"The heal never runs, so every damaged Container keeps showing the "
			"subcontractor's warehouse as its Accepted Warehouse.",
		)

	def test_it_is_importable(self):
		patch_module = frappe.get_module(
			"mhr.patches.v1_0.heal_container_accepted_warehouse"
		)
		self.assertTrue(callable(patch_module.execute))

	def test_it_reads_the_original_from_the_purchase_receipt(self):
		patch_module = frappe.get_module(
			"mhr.patches.v1_0.heal_container_accepted_warehouse"
		)
		source = inspect.getsource(
			patch_module._accepted_warehouse_from_purchase_receipts
		)
		self.assertIn("tabPurchase Receipt Item", source)
		self.assertIn("pr.docstatus = 1", source)
		self.assertIn("IFNULL(pr.is_return, 0) = 0", source)



class TestSupplierBatchNoIsVisibleBeforeSave(FrappeTestCase):
	"""The column was blank until save, so the ascending order was invisible.

	`fetch_from` resolves server-side on save, and a row built by add_child
	never goes through the model layer, so no client-side fetch fires.
	"""

	def setUp(self):
		self.script = _client_script(SE_SCRIPT, "Stock Entry")["script"]

	def test_the_bulk_fetch_row_sets_it(self):
		self.assertIn(
			"custom_supplier_batch_no: data.custom_supplier_batch_no,",
			self.script,
			"Fetch Batches rows land without a Supplier Batch No again — the "
			"column stays blank until save.",
		)

	def test_the_single_supplier_batch_path_still_sets_it(self):
		"""Untouched; pinned so the two paths cannot drift apart."""
		self.assertIn(
			"custom_supplier_batch_no: data.supplier_batch_no,", self.script
		)

	def test_the_server_returns_the_field(self):
		"""The client can only set what fetch_batches hands back."""
		source = inspect.getsource(note.fetch_batches)
		self.assertIn('"custom_supplier_batch_no"', source)

	def test_fetch_from_is_left_in_place(self):
		"""Both read the same source, so pre-filling cannot disagree with what
		save writes, and a row added by hand still gets filled."""
		path = frappe.get_app_path("mhr", "fixtures", "custom_field.json")
		with open(path, encoding="utf-8") as f:
			records = json.load(f)

		field = next(
			(
				r
				for r in records
				if r.get("dt") == "Stock Entry Detail"
				and r.get("fieldname") == "custom_supplier_batch_no"
			),
			None,
		)
		self.assertIsNotNone(field, "The custom field is gone from the fixture.")
		self.assertEqual(field.get("fetch_from"), "batch_no.custom_supplier_batch_no")


class TestFetchRefusesWithoutASourceWarehouse(FrappeTestCase):
	"""A missing warehouse means "any warehouse" on the server, which is the
	pre-fix behaviour. On the Stock Entry that must not happen quietly."""

	def setUp(self):
		self.script = _client_script(SE_SCRIPT, "Stock Entry")["script"]

	def test_it_stops_before_calling_the_server(self):
		self.assertIn("if (!source_warehouse) {", self.script)
		self.assertIn("Source Warehouse Required", self.script)

	def test_it_unticks_the_checkbox_so_it_can_be_retried(self):
		# The guard body runs from its `if` down to the fetch it is protecting.
		# Slicing at the first `}` instead cuts inside frappe.msgprint's own
		# options object, which is short of everything worth asserting.
		guard = self.script.split("if (!source_warehouse) {", 1)[1].split(
			"frappe.call(", 1
		)[0]
		self.assertIn("frm.set_value('custom_fetch_batches', 0);", guard)
		self.assertIn("return;", guard)

	def test_the_server_still_treats_it_as_optional(self):
		"""The guard belongs to the form; Delivery Note passes none on purpose."""
		signature = inspect.signature(note.fetch_batches)
		self.assertIsNone(signature.parameters["warehouse"].default)


class TestFetchedRowsCarryTheirWarehouses(FrappeTestCase):
	"""ERPNext propagates the default warehouses from those fields' own change
	handlers, so rows appended afterwards landed with both columns empty."""

	def setUp(self):
		self.script = _client_script(SE_SCRIPT, "Stock Entry")["script"]

	def test_the_row_gets_a_source_warehouse(self):
		self.assertIn(
			"s_warehouse: data.warehouse || frm.doc.from_warehouse,", self.script
		)

	def test_the_row_gets_a_target_warehouse(self):
		self.assertIn("t_warehouse: frm.doc.to_warehouse,", self.script)

	def test_the_server_hands_back_the_warehouse_to_use(self):
		"""data.warehouse is where the batch holds its balance (MI1-I78 P7)."""
		source = inspect.getsource(note._clamp_batch_qty_to_available)
		self.assertIn('b["warehouse"] = entry["warehouse"]', source)


class TestDisabledBatchesAreNeverOffered(FrappeTestCase):
	"""ERPNext never ticks Batch.disabled itself — it only ever means someone
	deliberately took that batch out of circulation."""

	def test_the_filter_is_applied(self):
		with patch.object(frappe, "get_all", return_value=[]) as get_all:
			note.fetch_batches(limit=5, container_no="MCJC-2222")
		self.assertEqual(get_all.call_args.kwargs["filters"]["disabled"], 0)

	def test_a_caller_passing_nothing_still_gets_nothing(self):
		"""The filter is added after the empty check, so it cannot turn a
		no-argument call into a scan of every batch on the site."""
		with patch.object(frappe, "get_all") as get_all:
			self.assertEqual(note.fetch_batches(limit=5, is_return=True), [])
		get_all.assert_not_called()


class TestLocationNoteHeal(FrappeTestCase):
	"""The same hooks overwrote the free-text Location notes. Those have no
	Purchase Receipt to fall back on, so the original is recovered from the
	container's own batches that were never moved."""

	def _module(self):
		return frappe.get_module("mhr.patches.v1_0.heal_container_location_notes")

	def test_it_is_registered_after_the_accepted_warehouse_heal(self):
		path = frappe.get_app_path("mhr", "patches.txt")
		with open(path, encoding="utf-8") as f:
			lines = [line.strip() for line in f]
		names = [line.split(" #")[0] for line in lines]
		self.assertIn("mhr.patches.v1_0.heal_container_location_notes", names)

	def test_the_surviving_value_is_the_original(self):
		rows = [
			{"batch": "B1", "location": ""},
			{"batch": "B2", "location": ""},
			{"batch": "B3", "location": "SURAMYA YARN - MC"},
		]
		self.assertEqual(
			self._module()._inward_location(rows, {"SURAMYA YARN - MC"}), ""
		)

	def test_it_gives_up_when_every_batch_was_moved(self):
		"""Nothing survives to read the original off."""
		rows = [{"batch": "B1", "location": "SURAMYA YARN - MC"}]
		self.assertIsNone(
			self._module()._inward_location(rows, {"SURAMYA YARN - MC"})
		)

	def test_it_gives_up_when_the_survivors_disagree(self):
		rows = [
			{"batch": "B1", "location": "rack 4"},
			{"batch": "B2", "location": "rack 9"},
		]
		self.assertIsNone(self._module()._inward_location(rows, set()))

	def test_batches_are_grouped_through_the_child_table(self):
		"""Several Container documents share one container_no (one per lot), so
		Batch.custom_container_no cannot tell them apart."""
		source = inspect.getsource(self._module()._batch_locations)
		self.assertIn("tabBatch Items", source)
		self.assertNotIn("custom_container_no", source)


class TestReceiveFromSubcontractorCanBeSaved(FrappeTestCase):
	"""MI1-I103: 'Receive from Subcontractor' built a draft that could not be
	saved — StockEntry.validate_warehouse runs from validate() and throws
	"Target warehouse is mandatory for row {0}" when the row and the header are
	both empty. The rows stay blank by design (MI1-I50, 2026-07-17); the header
	now carries a default, which ERPNext copies down itself."""

	def _source(self):
		return inspect.getsource(
			frappe.get_attr("mhr.utilis.make_receive_from_subcontractor")
		)

	def test_the_header_target_defaults_to_where_it_was_sent_from(self):
		self.assertIn("receipt.to_warehouse = source.from_warehouse or next(", self._source())

	def test_it_falls_back_to_a_row_when_the_send_had_no_header(self):
		self.assertIn(
			"(item.s_warehouse for item in source.items if item.s_warehouse)",
			self._source(),
		)

	def test_the_rows_are_still_left_blank(self):
		"""The 2026-07-17 decision stands — only the header gained a default."""
		self.assertIn('"t_warehouse": "",', self._source())

	def test_erpnext_still_owns_the_validation(self):
		"""Nothing here suppresses it; the header simply gives it an answer."""
		source = self._source()
		for escape in ("ignore_validate", "flags.ignore", "ignore_mandatory"):
			with self.subTest(escape=escape):
				self.assertNotIn(escape, source)
