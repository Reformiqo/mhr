"""MI1-I144 (2026-09-15) — "50-100+ row Delivery Notes time out on
save/submit ... this was not happening in the old server".

Three Delivery Note doc_events hooks each did one SQL round trip PER ROW
(a return row did two: a SELECT for the original DN Item's own cone, then
an UPDATE): `update_item_batch` / `reverse_item_batch` (on_submit /
on_cancel, every Delivery Note) and `restore_cones_for_hty_return`
(on_submit, HTY returns only). A 100-row note issued up to 200 round trips
just for these three hooks — fine on a low-latency setup, but each extra
round trip is pure overhead that scales linearly with row count, and any
increase in per-query latency (a DB on a different host, e.g.) multiplies
straight through it.

Fixed by aggregating each hook's per-row work into ONE batched UPDATE
(a CASE expression keyed on the row identifier) regardless of row count,
after resolving whatever per-row lookups are unavoidable (a return's
original DN Item cone; the target Batch Items row for an HTY return) in a
SINGLE batched read up front instead of one per row. Net effect on the
data is byte-for-byte identical — a batch (or Batch Items row) touched by
several Delivery Note rows nets to the exact same total change either way,
just via fewer round trips.

No real functional test existed for update_item_batch / reverse_item_batch
at all before this ticket (only a source-level pin in
test_stock_sheet_balance_report_v2_mi1_i135.py's own docstring, and a
mention in test_hty_server_hooks_mi1_i39.py's module docstring) — these
tests fill that gap as well as pinning the batching itself.
"""
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from mhr import utilis

ITEM = "MI1I144-ITEM"
COMPANY = "Meher Creations"


def _sql_calls(fn):
    """Runs fn() with frappe.db.sql wrapped to record every call's query
    text, returns (result, [query_texts]). Not a mock — real queries still
    execute. frappe.db.commit()'s own housekeeping ("commit", "START
    TRANSACTION") also routes through frappe.db.sql in this Frappe
    version, so a caller counting *data* queries should filter for that."""
    calls = []
    original_sql = frappe.db.sql

    def _tracking(*args, **kwargs):
        calls.append(args[0] if args else "")
        return original_sql(*args, **kwargs)

    with patch.object(frappe.db, "sql", side_effect=_tracking):
        result = fn()
    return result, calls


class _Row:
    """Lightweight stand-in for a Delivery Note Item row — these hooks
    only ever read .batch_no / .custom_cone / .custom_container_no /
    .dn_detail off each item, matching test_hty_server_hooks_mi1_i39.py's
    own established MagicMock-free pattern for update_item_batch's twin."""

    def __init__(self, batch_no=None, custom_cone=0, custom_container_no=None, dn_detail=None):
        self.batch_no = batch_no
        self.custom_cone = custom_cone
        self.custom_container_no = custom_container_no
        self.dn_detail = dn_detail


class _Doc:
    def __init__(self, items, is_return=0, transaction_type="VFY"):
        self.items = items
        self.is_return = is_return
        self.transaction_type = transaction_type


def _make_batch(name, cone):
    if frappe.db.exists("Batch", name):
        frappe.db.sql("DELETE FROM `tabBatch` WHERE name=%s", (name,))
    b = frappe.new_doc("Batch")
    b.batch_id = name
    b.item = ITEM
    b.custom_cone = cone
    b.custom_transaction_type = "VFY"
    b.flags.ignore_mandatory = True
    b.insert(ignore_permissions=True)
    return b.name


def _make_dn_row(cone):
    """A real (draft, unsubmitted) Delivery Note with one item row, purely
    to get a real Delivery Note Item name to use as a return row's
    dn_detail -- the authoritative source update_item_batch must prefer
    over the return row's own (possibly stale) custom_cone."""
    dn = frappe.new_doc("Delivery Note")
    dn.customer = frappe.db.get_value("Customer", {}, "name")
    dn.company = COMPANY
    dn.set_posting_time = 1
    dn.posting_date = frappe.utils.nowdate()
    dn.append("items", {"item_code": ITEM, "qty": 1, "rate": 10, "custom_cone": cone})
    dn.flags.ignore_validate = True
    dn.flags.ignore_mandatory = True
    dn.insert(ignore_permissions=True)
    return dn.name, dn.items[0].name


class TestUpdateAndReverseItemBatch(FrappeTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if not frappe.db.exists("Item", ITEM):
            frappe.get_doc({
                "doctype": "Item", "item_code": ITEM, "item_name": ITEM, "item_group": "Products",
                "stock_uom": "Nos", "is_stock_item": 1, "has_batch_no": 1, "create_new_batch": 0,
            }).insert(ignore_permissions=True)

    def setUp(self):
        self.batch_a = _make_batch("MI1I144-BATCH-A", 50)
        self.batch_b = _make_batch("MI1I144-BATCH-B", 30)

    def tearDown(self):
        frappe.db.sql("DELETE FROM `tabBatch` WHERE name IN (%s, %s)", (self.batch_a, self.batch_b))
        frappe.db.commit()

    def _cone(self, batch):
        return frappe.db.get_value("Batch", batch, "custom_cone")

    def test_regular_delivery_decrements_the_batch_cone(self):
        doc = _Doc([_Row(self.batch_a, 12)], is_return=0)
        utilis.update_item_batch(doc)
        self.assertEqual(self._cone(self.batch_a), 38)

    def test_two_rows_sharing_a_batch_net_into_the_same_total_change(self):
        """The exact scenario batching must get right: two Delivery Note
        rows against the SAME batch must net to the identical total the
        old sequential-UPDATE version produced (5 + 7 = 12 off, either way)."""
        doc = _Doc([_Row(self.batch_a, 5), _Row(self.batch_a, 7)], is_return=0)
        utilis.update_item_batch(doc)
        self.assertEqual(self._cone(self.batch_a), 50 - 12)

    def test_multiple_distinct_batches_each_update_correctly(self):
        doc = _Doc([_Row(self.batch_a, 10), _Row(self.batch_b, 4)], is_return=0)
        utilis.update_item_batch(doc)
        self.assertEqual(self._cone(self.batch_a), 40)
        self.assertEqual(self._cone(self.batch_b), 26)

    def test_return_increments_using_the_rows_own_cone_when_no_dn_detail(self):
        doc = _Doc([_Row(self.batch_a, 9)], is_return=1)
        utilis.update_item_batch(doc)
        self.assertEqual(self._cone(self.batch_a), 59)

    def test_return_prefers_the_original_dn_items_own_cone_over_the_rows(self):
        dn_name, dn_detail = _make_dn_row(cone=20)
        try:
            # The return row's OWN custom_cone (999) must be ignored in
            # favour of the original DN Item's cone (20) -- the
            # authoritative source, unchanged behaviour from before batching.
            doc = _Doc([_Row(self.batch_a, 999, dn_detail=dn_detail)], is_return=1)
            utilis.update_item_batch(doc)
            self.assertEqual(self._cone(self.batch_a), 70)
        finally:
            frappe.db.sql("DELETE FROM `tabDelivery Note Item` WHERE parent=%s", (dn_name,))
            frappe.db.sql("DELETE FROM `tabDelivery Note` WHERE name=%s", (dn_name,))
            frappe.db.commit()

    def test_rows_without_a_batch_are_skipped(self):
        doc = _Doc([_Row(None, 10), _Row(self.batch_a, 5)], is_return=0)
        utilis.update_item_batch(doc)
        self.assertEqual(self._cone(self.batch_a), 45)

    def test_no_batched_rows_issues_no_query_at_all(self):
        doc = _Doc([_Row(None, 10)], is_return=0)
        with patch.object(frappe.db, "sql", side_effect=AssertionError("must not query when nothing to update")):
            utilis.update_item_batch(doc)

    def test_many_rows_issue_exactly_one_update_query(self):
        """MI1-I144's actual point: a 100-row Delivery Note issued up to
        200 round trips before this fix. Pin that it is now O(1)."""
        rows = [_Row(self.batch_a, 1) for _ in range(100)]
        doc = _Doc(rows, is_return=0)
        _, calls = _sql_calls(lambda: utilis.update_item_batch(doc))
        self.assertEqual(len(calls), 1, "must issue exactly one UPDATE regardless of row count")
        self.assertEqual(self._cone(self.batch_a), 50 - 100)

    def test_reverse_undoes_a_regular_delivery_exactly(self):
        doc = _Doc([_Row(self.batch_a, 15), _Row(self.batch_a, 5)], is_return=0)
        utilis.update_item_batch(doc)
        self.assertEqual(self._cone(self.batch_a), 30)
        utilis.reverse_item_batch(doc)
        self.assertEqual(self._cone(self.batch_a), 50)

    def test_reverse_undoes_a_return_exactly(self):
        doc = _Doc([_Row(self.batch_a, 8)], is_return=1)
        utilis.update_item_batch(doc)
        self.assertEqual(self._cone(self.batch_a), 58)
        utilis.reverse_item_batch(doc)
        self.assertEqual(self._cone(self.batch_a), 50)

    def test_reverse_many_rows_also_issues_exactly_one_update_query(self):
        rows = [_Row(self.batch_a, 2) for _ in range(50)]
        doc = _Doc(rows, is_return=0)
        utilis.update_item_batch(doc)
        _, calls = _sql_calls(lambda: utilis.reverse_item_batch(doc))
        self.assertEqual(len(calls), 1)
        self.assertEqual(self._cone(self.batch_a), 50)


class TestRestoreConesForHtyReturnBatched(FrappeTestCase):
    """Real Container + Batch Items fixtures — restore_cones_for_hty_return
    joins across both tables, unlike update_item_batch's plain Batch
    update, so it needs its own real-data coverage."""

    CONTAINER_NO = "MI1I144-HTY-C"
    CONTAINER_NO_SHARED = "MI1I144-HTY-SHARED"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if not frappe.db.exists("Item", ITEM):
            frappe.get_doc({
                "doctype": "Item", "item_code": ITEM, "item_name": ITEM, "item_group": "Products",
                "stock_uom": "Nos", "is_stock_item": 1, "has_batch_no": 1, "create_new_batch": 0,
            }).insert(ignore_permissions=True)

    def _wipe(self):
        for r in frappe.get_all(
            "Container", filters={"container_no": ["in", (self.CONTAINER_NO, self.CONTAINER_NO_SHARED)]},
            pluck="name",
        ):
            frappe.db.sql("DELETE FROM `tabBatch Items` WHERE parent=%s", (r,))
            frappe.db.sql("DELETE FROM `tabContainer` WHERE name=%s", (r,))
        frappe.db.sql(
            "DELETE FROM `tabBatch` WHERE custom_container_no IN (%s, %s)",
            (self.CONTAINER_NO, self.CONTAINER_NO_SHARED),
        )
        frappe.db.commit()

    def setUp(self):
        self._wipe()

    def tearDown(self):
        self._wipe()

    def _make_container_with_batch_items(self, container_no, batch_id, cone):
        c = frappe.new_doc("Container")
        c.container_no = container_no
        c.lot_no = "L1"
        c.item = ITEM
        c.transaction_type = "HTY"
        c.posting_date = frappe.utils.nowdate()
        c.append("batches", {"batch_id": batch_id, "item": ITEM, "qty": 10, "cone": cone})
        c.flags.ignore_validate = True
        c.flags.ignore_mandatory = True
        c.insert(ignore_permissions=True)
        frappe.db.set_value("Container", c.name, "docstatus", 1, update_modified=False)
        row_name = frappe.db.get_value(
            "Batch Items", {"parent": c.name, "batch_id": batch_id}, "name"
        )
        return c.name, row_name

    def _cone(self, row_name):
        return frappe.db.get_value("Batch Items", row_name, "cone")

    def test_credits_the_correct_batch_items_row(self):
        _, row = self._make_container_with_batch_items(self.CONTAINER_NO, "MI1I144-HTY-B1", 5)
        doc = _Doc(
            [_Row("MI1I144-HTY-B1", custom_cone=3, custom_container_no=self.CONTAINER_NO)],
            is_return=1, transaction_type="HTY",
        )
        utilis.restore_cones_for_hty_return(doc)
        self.assertEqual(self._cone(row), 8)

    def test_non_return_is_a_no_op(self):
        _, row = self._make_container_with_batch_items(self.CONTAINER_NO, "MI1I144-HTY-B2", 5)
        doc = _Doc(
            [_Row("MI1I144-HTY-B2", custom_cone=3, custom_container_no=self.CONTAINER_NO)],
            is_return=0, transaction_type="HTY",
        )
        utilis.restore_cones_for_hty_return(doc)
        self.assertEqual(self._cone(row), 5)

    def test_vfy_transaction_type_is_a_no_op(self):
        _, row = self._make_container_with_batch_items(self.CONTAINER_NO, "MI1I144-HTY-B3", 5)
        doc = _Doc(
            [_Row("MI1I144-HTY-B3", custom_cone=3, custom_container_no=self.CONTAINER_NO)],
            is_return=1, transaction_type="VFY",
        )
        utilis.restore_cones_for_hty_return(doc)
        self.assertEqual(self._cone(row), 5)

    def test_two_return_rows_for_the_same_pair_net_correctly(self):
        _, row = self._make_container_with_batch_items(self.CONTAINER_NO, "MI1I144-HTY-B4", 10)
        doc = _Doc(
            [
                _Row("MI1I144-HTY-B4", custom_cone=2, custom_container_no=self.CONTAINER_NO),
                _Row("MI1I144-HTY-B4", custom_cone=3, custom_container_no=self.CONTAINER_NO),
            ],
            is_return=1, transaction_type="HTY",
        )
        utilis.restore_cones_for_hty_return(doc)
        self.assertEqual(self._cone(row), 15)

    def test_distinct_batch_container_pairs_each_credit_correctly(self):
        _, row1 = self._make_container_with_batch_items(self.CONTAINER_NO, "MI1I144-HTY-B5", 5)
        _, row2 = self._make_container_with_batch_items(self.CONTAINER_NO_SHARED, "MI1I144-HTY-B6", 8)
        doc = _Doc(
            [
                _Row("MI1I144-HTY-B5", custom_cone=1, custom_container_no=self.CONTAINER_NO),
                _Row("MI1I144-HTY-B6", custom_cone=2, custom_container_no=self.CONTAINER_NO_SHARED),
            ],
            is_return=1, transaction_type="HTY",
        )
        utilis.restore_cones_for_hty_return(doc)
        self.assertEqual(self._cone(row1), 6)
        self.assertEqual(self._cone(row2), 10)

    def test_many_rows_issue_exactly_two_queries(self):
        """One batched SELECT (resolve every (batch, container) pair's
        target row) plus one batched UPDATE — not 2*N round trips."""
        batch_ids = [f"MI1I144-HTY-MANY-{i}" for i in range(20)]
        rows_created = []
        for bid in batch_ids:
            c = frappe.new_doc("Container")
            c.container_no = self.CONTAINER_NO
            c.lot_no = "L1"
            c.item = ITEM
            c.transaction_type = "HTY"
            c.posting_date = frappe.utils.nowdate()
            c.append("batches", {"batch_id": bid, "item": ITEM, "qty": 10, "cone": 1})
            c.flags.ignore_validate = True
            c.flags.ignore_mandatory = True
            c.insert(ignore_permissions=True)
            frappe.db.set_value("Container", c.name, "docstatus", 1, update_modified=False)
            rows_created.append(
                frappe.db.get_value("Batch Items", {"parent": c.name, "batch_id": bid}, "name")
            )

        doc = _Doc(
            [_Row(bid, custom_cone=1, custom_container_no=self.CONTAINER_NO) for bid in batch_ids],
            is_return=1, transaction_type="HTY",
        )
        # frappe.db.commit()'s own housekeeping ("commit", "START
        # TRANSACTION") also routes through frappe.db.sql in this Frappe
        # version -- filter to the two real data queries this function
        # itself issues (a bare "commit"/transaction string is never one).
        _, all_calls = _sql_calls(lambda: utilis.restore_cones_for_hty_return(doc))
        data_calls = [c for c in all_calls if "tabBatch Items" in c]
        self.assertEqual(len(data_calls), 2, "must issue exactly one SELECT + one UPDATE regardless of row count")
        for row in rows_created:
            self.assertEqual(self._cone(row), 2)
