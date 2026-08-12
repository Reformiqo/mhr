"""MI1 (Raj 2026-07-20): Sales Order Booking Weight-mode allocation
must fetch ONLY complete batches — never partial-fetch to hit the
target weight exactly.

Raj's spec:
  * Enter required Weight.
  * Walk available batches in order.
  * If a batch's full qty fits within the remaining weight -> include it.
  * If it would exceed -> skip (don't partial-fetch, don't take part).
  * To include that batch, user must raise the entered weight.

The behaviour test seeds an item + 3 batches (10 / 30 / 25 kg) with
the same container and lot, then calls `mhr.sales_order.get_so_batches`
in Weight mode with several target weights and verifies the batches
returned + their allotted_qty exactly matches Raj's contract.
"""
import frappe
from frappe.tests.utils import FrappeTestCase


class TestGetSoBatchesWeightModeNoPartial(FrappeTestCase):

    ITEM = "MI1-SOTEST-ITEM"
    CONTAINER = "MI1-SOTEST-CONT"
    LOT = "MI1-SOTEST-LOT"
    # batches sorted by supplier_batch_no ascending
    #  qty=10 (SBN A) -> qty=30 (SBN B) -> qty=25 (SBN C)
    BATCHES = [
        ("MI1-SOTEST-B-A", "A", 10),
        ("MI1-SOTEST-B-B", "B", 30),
        ("MI1-SOTEST-B-C", "C", 25),
    ]

    def setUp(self):
        if not frappe.db.exists("Item", self.ITEM):
            item = frappe.new_doc("Item")
            item.item_code = self.ITEM
            item.item_name = self.ITEM
            item.item_group = frappe.db.get_value("Item Group", {}, "name")
            item.stock_uom = "Nos"
            item.has_batch_no = 1
            item.create_new_batch = 0
            item.insert(ignore_permissions=True)

        for name, sbn, qty in self.BATCHES:
            if frappe.db.exists("Batch", name):
                frappe.delete_doc("Batch", name, ignore_permissions=True, force=1)
            b = frappe.new_doc("Batch")
            b.batch_id = name
            b.item = self.ITEM
            b.batch_qty = qty
            b.custom_container_no = self.CONTAINER
            b.custom_lot_no = self.LOT
            b.custom_supplier_batch_no = sbn
            b.custom_cone = 5
            b.insert(ignore_permissions=True)
        frappe.db.commit()

    def tearDown(self):
        for name, _, _ in self.BATCHES:
            if frappe.db.exists("Batch", name):
                frappe.delete_doc("Batch", name, ignore_permissions=True, force=1)
        if frappe.db.exists("Item", self.ITEM):
            frappe.delete_doc("Item", self.ITEM, ignore_permissions=True, force=1)
        frappe.db.commit()

    def _call(self, qty):
        from mhr.sales_order import get_so_batches
        return get_so_batches(
            item_code=self.ITEM,
            container_no=self.CONTAINER,
            lot_no=self.LOT,
            qty=qty,
        )

    def test_weight_5_fetches_nothing(self):
        """Target 5 kg — smallest batch is 10 kg. Nothing fits without
        partial-splitting, so the response must be empty."""
        r = self._call(5)
        self.assertEqual(
            r, [],
            "5 kg target < smallest batch (10 kg): nothing may be "
            "fetched. Partial batches are forbidden.",
        )

    def test_weight_10_fetches_only_10kg_batch(self):
        r = self._call(10)
        self.assertEqual(len(r), 1)
        self.assertEqual(r[0]["name"], "MI1-SOTEST-B-A")
        self.assertEqual(float(r[0]["allotted_qty"]), 10.0)

    def test_weight_29_still_only_10kg_batch(self):
        """Target 29 kg: 10 kg fits, but adding 30 kg batch would land
        at 40 > 29 — skip it. 25 kg batch alone would land at 10+25=35
        > 29 — skip that too. Result: only the 10 kg batch."""
        r = self._call(29)
        self.assertEqual(len(r), 1)
        self.assertEqual(r[0]["name"], "MI1-SOTEST-B-A")
        self.assertEqual(float(r[0]["allotted_qty"]), 10.0)

    def test_weight_35_fetches_10_and_25(self):
        """Target 35 kg: 10 + 30 = 40 > 35 (skip B), 10 + 25 = 35 (fits).
        The loop skips B and includes C — total = 35 exactly."""
        r = self._call(35)
        names = sorted(b["name"] for b in r)
        self.assertEqual(names, ["MI1-SOTEST-B-A", "MI1-SOTEST-B-C"],
            "Weight 35 must fetch 10 + 25 (skipping 30) — total lands "
            "exactly on target without partial batches.")
        self.assertEqual(sum(float(b["allotted_qty"]) for b in r), 35.0)

    def test_weight_65_fetches_all_three(self):
        """Target 65 kg: 10 + 30 + 25 = 65. All three fit."""
        r = self._call(65)
        names = sorted(b["name"] for b in r)
        self.assertEqual(names, [
            "MI1-SOTEST-B-A", "MI1-SOTEST-B-B", "MI1-SOTEST-B-C",
        ])
        self.assertEqual(sum(float(b["allotted_qty"]) for b in r), 65.0)

    def test_weight_100_fetches_all_three_no_split(self):
        """Target 100 kg > available 65 kg: return all three (65) —
        never partial-fetch to reach 100."""
        r = self._call(100)
        total = sum(float(b["allotted_qty"]) for b in r)
        self.assertEqual(total, 65.0,
            "Target 100 with only 65 available must return the full "
            "65 (all three batches). No batch was partial-fetched.")

    def test_no_batch_returned_with_partial_qty(self):
        """Regression pin: for any weight target, EVERY returned row's
        allotted_qty must equal its batch_qty (proving no partials)."""
        for target in (10, 25, 30, 35, 40, 55, 65, 100):
            r = self._call(target)
            for b in r:
                self.assertEqual(
                    float(b["allotted_qty"]),
                    float(b["batch_qty"]),
                    f"Target={target}: batch {b['name']} was "
                    f"partial-fetched (allotted={b['allotted_qty']} "
                    f"vs full={b['batch_qty']}). Weight mode must "
                    f"never split batches.",
                )
