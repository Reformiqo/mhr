"""MI1-I96 (Raj 2026-08-13) — VFY Sales Order: Container-wise Lot popup shows
only lots with stock left to book.

  * lots with available qty <= 0 are not displayed;
  * "available" = on hand minus what open Sales Orders already hold;
  * filtering is on current stock, not on the lot merely existing.
"""
import inspect
import json
import os
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

FIXTURE = os.path.join(frappe.get_app_path("mhr"), "fixtures", "client_script.json")
# Real VFY containers on this bench (see the MI1-I96 survey): one with stocked
# and depleted lots side by side, one with every lot depleted.
MIXED, MIXED_STOCKED, MIXED_EMPTY = "MCJC-1448", {"24052025", "04082025"}, {"23052025"}
DEPLETED = "MCJC-1527"   # Raj's screenshot container: five lots, no stock


def _fixture_script():
    with open(FIXTURE, encoding="utf-8") as fh:
        return next(cs for cs in json.load(fh) if cs["name"] == "Sales Order Booking")


class TestWithStockDropsDepletedLots(FrappeTestCase):

    def setUp(self):
        if not frappe.db.exists("Container", {"container_no": MIXED, "docstatus": 1}):
            self.skipTest(f"{MIXED} not on this bench.")

    def test_plain_call_still_lists_every_lot(self):
        from mhr.sales_order import get_container_details
        lots = {r["lot_no"] for r in get_container_details(MIXED)}
        self.assertTrue((MIXED_STOCKED | MIXED_EMPTY) <= lots, "Default call is unchanged (MI1-I91 pin).")

    def test_with_stock_keeps_only_lots_that_hold_stock(self):
        from mhr.sales_order import get_container_details
        rows = get_container_details(MIXED, with_stock=1)
        lots = {r["lot_no"] for r in rows}
        self.assertEqual(lots & MIXED_EMPTY, set(), "A depleted lot must not be offered.")
        self.assertTrue(MIXED_STOCKED <= lots)
        for r in rows:
            self.assertGreater(r["available_qty"], 0)
            self.assertIn("item", r)

    def test_container_with_no_stock_anywhere_offers_nothing(self):
        from mhr.sales_order import get_container_details
        if not frappe.db.exists("Container", {"container_no": DEPLETED, "docstatus": 1}):
            self.skipTest(f"{DEPLETED} not on this bench.")
        self.assertTrue(get_container_details(DEPLETED), "Lots exist against the container ...")
        self.assertEqual(get_container_details(DEPLETED, with_stock=1), [], "... but none has stock.")


class TestWithStockSubtractsOpenBookings(FrappeTestCase):
    """The 'consumed/booked' half of Raj's rule."""

    def setUp(self):
        if not frappe.db.exists("Container", {"container_no": MIXED, "docstatus": 1}):
            self.skipTest(f"{MIXED} not on this bench.")

    def test_fully_booked_lot_disappears(self):
        from mhr import sales_order
        from mhr.utilis import get_container_batches_with_stock
        stocked = get_container_batches_with_stock(MIXED)
        target = sorted(MIXED_STOCKED)[0]
        heavy = {b["name"]: float(b["batch_qty"]) for b in stocked if b["custom_lot_no"] == target}
        self.assertTrue(heavy)
        with patch.object(sales_order, "_booked_qty_by_batch", return_value=heavy):
            lots = {r["lot_no"] for r in sales_order.get_container_details(MIXED, with_stock=1)}
        self.assertNotIn(target, lots, "Every batch of the lot booked in full -> lot not offered.")
        self.assertTrue((MIXED_STOCKED - {target}) <= lots, "Other stocked lots unaffected.")

    def test_partial_booking_reduces_available_qty(self):
        from mhr import sales_order
        from mhr.utilis import get_container_batches_with_stock
        stocked = get_container_batches_with_stock(MIXED)
        target = sorted(MIXED_STOCKED)[0]
        mine = [b for b in stocked if b["custom_lot_no"] == target]
        full = sum(float(b["batch_qty"]) for b in mine)
        first = mine[0]
        with patch.object(sales_order, "_booked_qty_by_batch", return_value={first["name"]: float(first["batch_qty"]) / 2}):
            row = next(r for r in sales_order.get_container_details(MIXED, with_stock=1) if r["lot_no"] == target)
        self.assertAlmostEqual(row["available_qty"], full - float(first["batch_qty"]) / 2, places=2)

    def test_booking_rule_matches_get_available_qty(self):
        """One rule for the popup and the per-batch allocation."""
        from mhr import sales_order
        for fn in (sales_order._booked_qty_by_batch, sales_order._get_available_qty):
            src = inspect.getsource(fn)
            self.assertIn("SUM(soi.qty - soi.delivered_qty)", src)
            self.assertIn("so.status IN ('To Deliver and Bill', 'To Deliver', 'To Bill', 'Partially Delivered')", src)
            self.assertIn("so.docstatus = 1", src)
        self.assertEqual(sales_order._booked_qty_by_batch([]), {})
        self.assertEqual(sales_order._booked_qty_by_batch(["__no_such_batch__"]), {})


class TestVfyBookingScript(FrappeTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.cs = _fixture_script()
        cls.src = cls.cs["script"].replace("\r\n", "\n")

    def test_popup_asks_for_bookable_lots_only(self):
        i = self.src.find("function mi1_so_open_lot_picker(frm)")
        self.assertGreater(i, -1)
        body = self.src[i:i + 900]
        self.assertIn("method: 'mhr.sales_order.get_container_details'", body)
        self.assertIn("args: { container_no, with_stock: 1 },", body)
        self.assertNotIn("transaction_type:", body, "VFY stays unscoped by mode — legacy containers must keep appearing.")

    def test_empty_result_is_explained(self):
        self.assertEqual(self.src.count("function mi1_so_explain_no_available_lot(container_no)"), 1)
        self.assertIn("mi1_so_explain_no_available_lot(container_no);", self.src)
        self.assertIn("none with stock left to book", self.src)
        self.assertIn("No lots found for container {0}", self.src, "Unknown number keeps its old message.")

    def test_popup_columns_unchanged(self):
        self.assertIn("<th>Lot No</th><th>Item</th>", self.src)

    def test_fixture_bumped_and_matches_db(self):
        self.assertGreater(str(self.cs["modified"]), "2026-09-04")
        self.assertEqual(self.cs["enabled"], 1)
        db = (frappe.db.get_value("Client Script", "Sales Order Booking", "script") or "").replace("\r\n", "\n")
        self.assertEqual(db, self.src)
