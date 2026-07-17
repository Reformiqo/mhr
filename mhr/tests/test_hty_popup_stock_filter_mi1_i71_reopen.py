"""MI1-I71 reopen (Raj 2026-07-13):

  1. HTY popup rows must be sorted by Supplier Batch No ascending
     (smallest → largest).
  2. Multi-item DN submit was failing with BatchNegativeStockError
     because the popup listed batches that were already fully
     delivered. Fix: route the popup through a stock-aware server
     helper that returns only batches with positive available balance.
"""
import inspect
import json
import os

import frappe
from frappe.tests.utils import FrappeTestCase


def _hty_vfy_script():
    path = os.path.join(frappe.get_app_path("mhr"), "fixtures", "client_script.json")
    with open(path) as fh:
        data = json.load(fh)
    for cs in data:
        if cs.get("name") == "HTY & VFY":
            return cs.get("script", "")
    raise AssertionError("HTY & VFY script missing from fixtures.")


class TestSortAscendingBySupplierBatchNo(FrappeTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.src = _hty_vfy_script()

    def test_marker_present(self):
        self.assertIn(
            "MI1-I71 reopen (Raj 2026-07-13)",
            self.src,
            "HTY popup dialog code must carry the MI1-I71 reopen marker.",
        )

    def test_batches_sorted_by_sbn(self):
        self.assertIn("batches.sort(", self.src,
            "show_hty_batch_dialog must sort batches[] before rendering.")
        self.assertIn(
            "a.custom_supplier_batch_no",
            self.src,
            "Sort must key off custom_supplier_batch_no.",
        )

    def test_sort_is_numeric_aware(self):
        """'4486' vs '7004' as strings sort correctly but '10' would
        sort before '9' — must use { numeric: true }."""
        self.assertIn("numeric: true", self.src,
            "Sort must pass { numeric: true } to localeCompare so "
            "4486/4487/../7004 sorts numerically.")

    def test_empty_sbn_sinks_to_bottom(self):
        """A batch missing SBN should render last, not first."""
        self.assertIn("if (!sa && !sb) return 0;", self.src)
        self.assertIn("if (!sa) return 1;", self.src)
        self.assertIn("if (!sb) return -1;", self.src)


class TestStockAwareBatchFetch(FrappeTestCase):

    def test_server_helper_defined_and_whitelisted(self):
        from mhr import utilis
        fn = getattr(utilis, "get_container_batches_with_stock", None)
        self.assertTrue(callable(fn),
            "mhr.utilis.get_container_batches_with_stock must exist.")
        # frappe.whitelist() registers callables in frappe.whitelisted.
        self.assertIn(
            fn, frappe.whitelisted,
            "get_container_batches_with_stock must be @frappe.whitelist() "
            "so the client can call it.",
        )

    def test_helper_reads_from_sbb(self):
        from mhr import utilis
        src = inspect.getsource(utilis.get_container_batches_with_stock)
        self.assertIn("tabSerial and Batch Bundle", src,
            "Helper must query SBB — SLE.batch_no isn't indexed for "
            "batchwise-valuation batches.")
        self.assertIn("tabSerial and Batch Entry", src)

    def test_helper_filters_positive_balance_only(self):
        from mhr import utilis
        src = inspect.getsource(utilis.get_container_batches_with_stock)
        self.assertIn("HAVING balance > 0", src,
            "Helper must drop rows with zero or negative net balance — "
            "those are already-delivered batches that would trigger "
            "BatchNegativeStockError on submit.")

    def test_helper_returns_empty_for_empty_input(self):
        from mhr.utilis import get_container_batches_with_stock
        self.assertEqual(get_container_batches_with_stock(None), [])
        self.assertEqual(get_container_batches_with_stock(""), [])

    def test_client_get_all_batches_routes_through_helper(self):
        src = _hty_vfy_script()
        self.assertIn(
            "method: 'mhr.utilis.get_container_batches_with_stock'",
            src,
            "The HTY & VFY script's get_all_batches must route through "
            "mhr.utilis.get_container_batches_with_stock so zero-stock "
            "batches never enter the popup.",
        )

    def test_client_no_longer_uses_frappe_client_get_list_for_container(self):
        """The old get_all_batches paginated frappe.client.get_list —
        that returned zero-stock batches. Pin its removal so a future
        edit doesn't reintroduce the bug."""
        src = _hty_vfy_script()
        # Extract the get_all_batches function body specifically.
        start = src.find("async function get_all_batches(container_no)")
        self.assertGreater(start, -1, "get_all_batches must exist.")
        depth = 0
        i = start
        end = start
        while i < len(src):
            ch = src[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
            i += 1
        body = src[start:end]
        self.assertNotIn(
            "method: 'frappe.client.get_list'",
            body,
            "get_all_batches must not use frappe.client.get_list — it "
            "returned zero-stock batches. Route through the mhr helper.",
        )


class TestGetAllBatchesByItemAlsoRoutesThroughServer(FrappeTestCase):
    """MI1-I71 (Raj 2026-07-17): the SIBLING helper
    (get_all_batches_by_item) was also rewired — see
    test_hty_popup_clamped_batch_qty_mi1_i71.py for the full pin.

    The earlier MI1-I71 reopen (2026-07-13) only fixed the container-
    scoped fetcher, but the same stale-batch_qty bug still hit the
    item-scoped (custom_denier) path. Both paths now route through
    server helpers so `batch_qty` reflects SBB balance, not the raw
    Batch master value.
    """

    def test_get_all_batches_by_item_routes_through_server_helper(self):
        src = _hty_vfy_script()
        start = src.find("async function get_all_batches_by_item(item)")
        self.assertGreater(start, -1)
        body = src[start:start + 1500]
        self.assertNotIn(
            "method: 'frappe.client.get_list'",
            body,
            "get_all_batches_by_item must NOT use raw frappe.client.get_list "
            "— that returned the stale Batch master `batch_qty` which "
            "over-drafted partially-consumed batches on Submit. Route via "
            "mhr.note.get_hty_batches_by_item (MI1-I71, 2026-07-17).",
        )
        self.assertIn(
            "mhr.note.get_hty_batches_by_item",
            body,
            "get_all_batches_by_item must call mhr.note.get_hty_batches_by_item "
            "(the clamp-aware server endpoint).",
        )
