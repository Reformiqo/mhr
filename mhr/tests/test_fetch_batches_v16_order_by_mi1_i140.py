"""MI1-I140 (Raj 2026-09-14) — "if count is entered and then the fetch is
toggled it shows this error": Invalid field format in Order By:
CAST(custom_supplier_batch_no AS UNSIGNED). Use 'field', 'link_field.field',
or 'child_table.field'.

Root cause: `mhr.note.fetch_batches` (the "Count"-driven Fetch Batches flow)
used `frappe.get_all(..., order_by="CAST(custom_supplier_batch_no AS
UNSIGNED) asc, ...")` to keep the scan window numerically ordered (MI1-I124
— the column is Data, so a plain string order put '10' before '2'). Frappe
v16 added strict order_by validation (frappe.database.query.py ::
SIMPLE_FIELD_PATTERN) that only accepts a bare field name or a dotted
link_field.field / child_table.field reference — a raw SQL expression like
this CAST throws immediately. That validation is completely absent from
v15 (confirmed: grepping the exact error string finds it only under
apps/frappe on the v16 bench, zero hits on v15) — this bench had been
running fine on v15 the whole time, and the report surfaced while testing
on a v16 site (meher.nbg.frappe.cloud).

Fix: moved _scan()'s query from frappe.get_all to frappe.db.sql, which was
never subject to that ORM-level order_by validation on either version — the
exact same CAST-based ordering, filters and or_filters, just built as
parameterized raw SQL instead of get_all kwargs.
"""
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from mhr import note

ITEM = "MI1I140-TEST-ITEM"
CONTAINER_NO = "MI1I140-TEST"
LOT_NO = "L1"


def _wipe():
    frappe.db.sql("DELETE FROM `tabBatch` WHERE custom_container_no=%s", (CONTAINER_NO,))
    frappe.db.commit()


class TestFetchBatchesNeverCallsGetAll(FrappeTestCase):
    """The whole point of the fix: get_all (and its order_by validation,
    v16-specific or not) must never be reached by this function again."""

    def test_source_has_no_get_all_call(self):
        import inspect
        src = inspect.getsource(note.fetch_batches)
        self.assertNotIn("frappe.get_all(", src)

    def test_order_by_string_is_never_passed_to_the_orm(self):
        """A belt-and-suspenders check: even if get_all were reintroduced
        elsewhere, this specific CAST expression must never flow through
        frappe.get_all's own order_by= kwarg again."""
        import inspect
        src = inspect.getsource(note.fetch_batches)
        self.assertNotIn('order_by="CAST(', src)


class TestNumericOrderingStillWorksOnRealData(FrappeTestCase):
    """MI1-I124's own regression, end to end: batches with supplier batch
    numbers '1' through '14' must scan-window in numeric order (1, 2, 3...),
    not string order (1, 10, 11, 12, 13, 14, 2, 3...)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _wipe()
        if not frappe.db.exists("Item", ITEM):
            frappe.get_doc({
                "doctype": "Item", "item_code": ITEM, "item_name": ITEM, "item_group": "Products",
                "stock_uom": "Nos", "is_stock_item": 1, "has_batch_no": 1, "create_new_batch": 0,
            }).insert(ignore_permissions=True)
        cls.docs = []
        for n in range(1, 15):  # supplier batch nos "1".."14" -- the exact MI1-I124 shape
            batch_id = f"MI1I140-{n}"
            if frappe.db.exists("Batch", batch_id):
                frappe.db.sql("DELETE FROM `tabBatch` WHERE name=%s", (batch_id,))
            b = frappe.new_doc("Batch")
            b.batch_id = batch_id
            b.item = ITEM
            b.custom_container_no = CONTAINER_NO
            b.custom_lot_no = LOT_NO
            b.custom_cone = 6
            b.custom_supplier_batch_no = str(n)
            b.custom_transaction_type = "VFY"
            b.batch_qty = 25.0
            b.flags.ignore_mandatory = True
            b.insert(ignore_permissions=True)
            cls.docs.append(b.name)
        frappe.db.commit()

    @classmethod
    def tearDownClass(cls):
        _wipe()
        super().tearDownClass()

    def _fetch(self, **kw):
        # These batches carry no real Serial and Batch Bundle stock (no
        # transaction was ever posted for them) -- _clamp_batch_qty_to_
        # available would zero every one of them out, which is irrelevant
        # to what this test is actually checking (the ORDER BY itself).
        with patch.object(note, "_clamp_batch_qty_to_available", lambda *a, **k: None):
            return note.fetch_batches(**kw)

    def test_fetch_batches_does_not_raise(self):
        """The exact user-facing symptom: entering a Count and toggling
        Fetch Batches must not throw "Invalid field format in Order By"."""
        self._fetch(limit=10, container_no=CONTAINER_NO, lot_no=LOT_NO, cone=6)  # must not raise

    def test_count_10_returns_1_through_10_not_string_order(self):
        out = self._fetch(limit=10, container_no=CONTAINER_NO, lot_no=LOT_NO, cone=6)
        got = [b["custom_supplier_batch_no"] for b in out]
        self.assertEqual(got, [str(n) for n in range(1, 11)])
