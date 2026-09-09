"""MI1-I116 (Raj 2026-08-31) — Merge No must be per-batch, never the note's
or a container-master lookup keyed on (container_no, lot_no).

Round 1: Delivery Note Lot-Wise selected `dn.custom_merge_no` — the
note-level aggregate that shows the first container's value on every lot.

Round 2 (0b370d3): resolved through `dn.dn._merge_numbers_by_container_and_lot`,
the same Container-master lookup the DN report used.

Round 3 (MI1-I132, 2026-09-09): that lookup is itself wrong whenever more
than one Container document shares a (container_no, lot_no) pair — real
data on this bench: MCJC-1630/lot 11122025 carries two Container docs,
MCJC-1630-2292 (Merge No S5dx, item 58D/24F) and MCJC-1630-2296 (Merge No
H38x, item 75D/30f). The lookup comma-joined them ("H38x, S5dx") on every
row for that container+lot instead of each row's own value.
`_merge_numbers_by_container_and_lot` / `_container_lot_key` are gone;
Merge No is read per-row from the linked Batch, exactly like the DN report
(`dn.py`'s Pulp / Glue / Lusture / Grade columns).
"""
import inspect

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import today

from mhr.mhr.report.dn import dn as dn_report
from mhr.mhr.report.delivery_note_lot_wise import delivery_note_lot_wise as lot_wise

CONTAINER = "MCJC-1630"
LOT = "11122025"
# Real local batches: same container_no + lot_no, different Container docs.
BATCH_S5DX = "MCJC-163011122025752"   # -> Container MCJC-1630-2292, item 58D/24F
BATCH_H38X = "MCJC-163011122025239"   # -> Container MCJC-1630-2296, item 75D/30f
WAREHOUSE = "Finished Goods - MC"


class TestLotWiseNoLongerReadsTheHeaderOrTheContainerMaster(FrappeTestCase):

    def test_header_field_is_gone_from_the_query(self):
        src = inspect.getsource(lot_wise.get_data)
        self.assertNotIn("dn.custom_merge_no", src, "Raj: never from the parent Delivery Note field.")

    def test_the_container_master_resolver_is_gone(self):
        self.assertFalse(hasattr(lot_wise, "_merge_numbers_by_container_and_lot"))
        self.assertFalse(hasattr(lot_wise, "_container_lot_key"))
        self.assertFalse(hasattr(dn_report, "_merge_numbers_by_container_and_lot"))
        self.assertFalse(hasattr(dn_report, "_container_lot_key"))

    def test_merge_no_comes_from_the_joined_batch(self):
        src = inspect.getsource(lot_wise.get_data)
        self.assertIn("LEFT JOIN `tabBatch` b ON b.name = dni.batch_no", src)
        self.assertIn("MAX(b.custom_merge_no)", src)

    def test_column_still_there(self):
        self.assertIn("merge_no", [c["fieldname"] for c in lot_wise.get_columns()])


class TestLotWiseOnRealData(FrappeTestCase):
    """MCJC-1630/lot 11122025 carries two Container documents with
    different Merge Nos (2292 -> S5dx, 2296 -> H38x). Deliver ONLY the
    batch that belongs to 2292: this report groups rows by
    (container_no, lot_no) alone, with no item in the key, so the OLD
    Container-master lookup — keyed the same way — pulled in BOTH
    Container docs sharing that pair regardless of which one this row's
    batch actually came from, and comma-joined "H38x, S5dx" even though
    only S5dx's batch was ever shipped. Reading Merge No off the row's own
    linked Batch fixes this without needing two items in one row."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        import unittest
        if not frappe.db.exists("Batch", BATCH_S5DX):
            raise unittest.SkipTest(f"Expected local Batch {BATCH_S5DX} not found on this bench.")
        container_docs = frappe.get_all(
            "Container",
            filters={"container_no": CONTAINER, "lot_no": LOT, "docstatus": 1},
            fields=["name", "merge_no"],
        )
        if len({c.merge_no for c in container_docs}) < 2:
            raise unittest.SkipTest("Expected two Container docs on this container/lot with different Merge Nos.")

        item = frappe.db.get_value("Batch", BATCH_S5DX, "item")
        cls.dn = frappe.new_doc("Delivery Note")
        cls.dn.update({
            "customer": frappe.db.get_value("Customer", {}, "name"),
            "company": "Meher Creations", "transaction_type": "VFY",
            "posting_date": today(), "set_posting_time": 1, "set_warehouse": WAREHOUSE,
            "selling_price_list": "Standard Selling", "currency": "INR",
            "custom_sales_person": "Jayendrabhai",
        })
        cls.dn.append("items", {
            "item_code": item, "qty": 1, "rate": 100, "warehouse": WAREHOUSE,
            "batch_no": BATCH_S5DX, "use_serial_batch_fields": 1,
            "custom_container_no": CONTAINER, "custom_lot_no": LOT,
        })
        cls.dn.insert(ignore_permissions=True)
        cls.dn.submit()

    @classmethod
    def tearDownClass(cls):
        cls.dn.reload()
        if cls.dn.docstatus == 1:
            cls.dn.cancel()
        super().tearDownClass()

    def test_row_matches_its_own_batch_not_the_sibling_containers_value(self):
        _, rows = lot_wise.execute(frappe._dict(delivery_note=self.dn.name))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["merge_no"], "S5dx",
                         "only the container this batch actually came from — never comma-joined with H38x")
