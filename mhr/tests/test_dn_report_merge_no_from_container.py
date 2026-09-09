"""The DN report's Merge No came off the Delivery Note, not the Batch.

Round 1 (MI1-I64 reopen / never shipped as such): the query selected
`dn.custom_merge_no`, which set_header_container_info_from_items fills by
aggregating the note's own rows. Being per-note it showed the same value on
every row of the note whatever that row's container was.

Round 2 (MI1-I116, 2026-08-31): fixed by resolving Merge No from the
Container master keyed on (container_no, lot_no) afterwards
(`_merge_numbers_by_container_and_lot`). That is the same fault the
Pulp / Glue / Lusture / Grade columns were already fixed for — the comment
above them says so:

	Batch attributes MUST be per-row from the linked Batch — NOT from the DN
	header... every row showed the same (aggregated) header value.

— but a (container_no, lot_no) pair is not unique to one Container document.
MCJC-1630 carries two: MCJC-1630-2292 (lot 11122025, Merge No S5dx, item
58D/24F) and MCJC-1630-2296 (lot 11122025, Merge No H38x, item 75D/30f) — two
different items, same container_no and lot_no, submitted as two separate
Container documents. A DN report row for either one showed the comma-joined
"H38x, S5dx" instead of its own batch's actual value (MI1-I132).

Round 3 (MI1-I132, 2026-09-09): Merge No is now read per-row from the linked
Batch — `Container.create_batches()` stamps `custom_merge_no` on every batch
it creates from `self.merge_no`, so it is exact and unambiguous, exactly like
Pulp / Glue / Lusture / Grade. The Container-master lookup
(`_merge_numbers_by_container_and_lot` / `_container_lot_key`) is gone.
"""

import inspect

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import today

from mhr.mhr.report.dn import dn as dn_report

CONTAINER = "MCJC-1630"
LOT = "11122025"
# Real local batches: same container_no + lot_no, different Container docs.
BATCH_S5DX = "MCJC-163011122025752"   # -> Container MCJC-1630-2292, item 58D/24F
BATCH_H38X = "MCJC-163011122025239"   # -> Container MCJC-1630-2296, item 75D/30f
WAREHOUSE = "Finished Goods - MC"


def _query_source():
	"""The SQL string only — the docstrings around it name the old field."""
	source = inspect.getsource(dn_report.get_data)
	return source.split("frappe.db.sql(", 1)[1].split("as_dict=True", 1)[0]


class TestTheQueryNoLongerReadsTheDeliveryNoteHeaderOrTheContainerMaster(FrappeTestCase):
	def test_custom_merge_no_is_gone_from_the_query(self):
		self.assertNotIn("dn.custom_merge_no", _query_source())

	def test_merge_no_comes_from_the_joined_batch(self):
		self.assertIn("MAX(b.custom_merge_no)", _query_source())

	def test_the_container_master_resolver_is_gone(self):
		self.assertFalse(hasattr(dn_report, "_merge_numbers_by_container_and_lot"))
		self.assertFalse(hasattr(dn_report, "_container_lot_key"))

	def test_the_column_is_still_vfy_only(self):
		"""Existing behaviour (MI1-I64 reopen): Merge No is a VFY concept and
		is dropped in HTY, exactly as the Balance Report drops it."""
		vfy = [c["fieldname"] for c in dn_report.get_columns({"transaction_type": "VFY"})]
		hty = [c["fieldname"] for c in dn_report.get_columns({"transaction_type": "HTY"})]
		self.assertIn("merge_no", vfy)
		self.assertNotIn("merge_no", hty)


class TestTheMergeNumberIsResolvedPerRowFromRealData(FrappeTestCase):
	"""The reported case, reproduced with real local Container/Batch master
	data (not mocked): MCJC-1630/lot 11122025 carries two Container
	documents with different Merge Nos. Two separate Delivery Notes each ship
	one of the two batches — a mixed-item note is its own unrelated
	constraint (Denier is a header Link field and cannot hold two items) —
	and each report row must show only its own batch's Merge No, never the
	other one, never both comma-joined."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		import unittest
		for batch in (BATCH_S5DX, BATCH_H38X):
			if not frappe.db.exists("Batch", batch):
				raise unittest.SkipTest(f"Expected local Batch {batch} not found on this bench.")
		container_docs = frappe.get_all(
			"Container",
			filters={"container_no": CONTAINER, "lot_no": LOT, "docstatus": 1},
			fields=["name", "merge_no"],
		)
		if len({c.merge_no for c in container_docs}) < 2:
			raise unittest.SkipTest("Expected two Container docs on this container/lot with different Merge Nos.")

		cls.dns = [cls._make_dn(batch) for batch in (BATCH_S5DX, BATCH_H38X)]

	@classmethod
	def _make_dn(cls, batch):
		item = frappe.db.get_value("Batch", batch, "item")
		dn = frappe.new_doc("Delivery Note")
		dn.update({
			"customer": frappe.db.get_value("Customer", {}, "name"),
			"company": "Meher Creations", "transaction_type": "VFY",
			"posting_date": today(), "set_posting_time": 1, "set_warehouse": WAREHOUSE,
			"selling_price_list": "Standard Selling", "currency": "INR",
			"custom_sales_person": "Jayendrabhai",
		})
		dn.append("items", {
			"item_code": item, "qty": 1, "rate": 100, "warehouse": WAREHOUSE,
			"batch_no": batch, "use_serial_batch_fields": 1,
			"custom_container_no": CONTAINER, "custom_lot_no": LOT,
		})
		dn.insert(ignore_permissions=True)
		dn.submit()
		return dn

	@classmethod
	def tearDownClass(cls):
		for dn in cls.dns:
			dn.reload()
			if dn.docstatus == 1:
				dn.cancel()
		super().tearDownClass()

	def test_each_notes_row_shows_only_its_own_batchs_merge_no(self):
		_, rows = dn_report.execute(frappe._dict(
			from_date=today(), to_date=today(), transaction_type="VFY",
		))
		by_name = {dn.name: [r for r in rows if dn.name in r["id"]] for dn in self.dns}
		for dn in self.dns:
			self.assertEqual(len(by_name[dn.name]), 1)
		s5dx_row, h38x_row = by_name[self.dns[0].name][0], by_name[self.dns[1].name][0]
		self.assertEqual(s5dx_row["merge_no"], "S5dx")
		self.assertEqual(h38x_row["merge_no"], "H38x")


class TestSupplierBatchNoIsUnchanged(FrappeTestCase):
	"""Asked about alongside Merge No, and deliberately left alone: it is not
	the same shape of bug.

	GROUP_CONCAT(DISTINCT dni.custom_supplier_batch_no) aggregates the Delivery
	Note ITEM rows inside this report row's own group, so the several values it
	shows genuinely belong to that row. Merge No was read from the note's
	header (and later the Container master keyed on container+lot) instead,
	both of which are values from outside the row's scope.
	"""

	def test_it_still_aggregates_the_rows_own_delivery_note_items(self):
		self.assertIn(
			"GROUP_CONCAT(DISTINCT dni.custom_supplier_batch_no SEPARATOR ', ')",
			_query_source(),
		)

	def test_it_is_not_read_from_the_delivery_note_header(self):
		self.assertNotIn("dn.custom_supplier_batch_no", _query_source())
