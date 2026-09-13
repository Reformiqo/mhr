"""MI1-I139 follow-up (Raj 2026-09-13, flagged priority) — "not able to
submit my delivery note... UOM Conversion Factor is required in every row."

Root cause: the "Fetch Batches" Client Script builds Delivery Note Item
rows with `frm.add_child("items", {..., uom: data.stock_uom})` — no
`conversion_factor`. `frm.add_child()` fires no grid event (the same
gotcha `ensure_total_qty` already works around for `total_qty`), so
ERPNext's own client-side UOM-change fetch never runs and the row reaches
save with `conversion_factor` unset — a `reqd` field on Delivery Note
Item, so the whole document was blocked.

Two-part fix, matching the client + server pattern this app already uses
for `total_qty`:
  * Client Script: the `frm.add_child()` call now also sets
    `conversion_factor: 1` directly — always correct here since `uom` is
    always set to the item's own stock_uom in that same call.
  * Server: `mhr.utilis.ensure_conversion_factor` (Delivery Note `validate`,
    via `calculate_delivery_note_totals`) backfills any row that still
    arrives without one — a defensive fallback for any OTHER path that
    builds rows the same way (a mapper, bulk import, a future script),
    reusing ERPNext's own `get_conversion_factor` rather than a hardcoded 1
    so a genuinely different sales UOM still resolves correctly.
"""
from unittest.mock import MagicMock

import frappe
from frappe.tests.utils import FrappeTestCase

from mhr.utilis import ensure_conversion_factor

ITEM = "58D/8F"  # stock_uom Nos, sales_uom Kg — a real bench master with only
                 # a "Nos" UOM Conversion Detail row, matching the ticket's data.


def _item_present():
    return frappe.db.exists("Item", ITEM)


class TestEnsureConversionFactorUnit(FrappeTestCase):

    def setUp(self):
        if not _item_present():
            self.skipTest("Bench lacks Item 58D/8F this suite is built on.")

    def _make_doc(self, items):
        """Top-level doc is a MagicMock, not frappe._dict — a _dict's own
        `.items` is the dict method, which would shadow the child-table
        attribute this function reads (mhr's established gotcha; see the
        identical pattern in test_delivery_note_totals.py)."""
        doc = MagicMock()
        doc.items = [frappe._dict(row) for row in items]
        return doc

    def test_fills_blank_conversion_factor_when_uom_is_stock_uom(self):
        doc = self._make_doc([{"item_code": ITEM, "uom": "Nos", "conversion_factor": None}])
        ensure_conversion_factor(doc)
        self.assertEqual(doc.items[0].conversion_factor, 1.0)

    def test_fills_zero_conversion_factor(self):
        doc = self._make_doc([{"item_code": ITEM, "uom": "Nos", "conversion_factor": 0}])
        ensure_conversion_factor(doc)
        self.assertEqual(doc.items[0].conversion_factor, 1.0)

    def test_leaves_an_already_set_factor_untouched(self):
        """A value ERPNext (or the form) already settled — including a
        genuinely non-default factor — must not be overwritten."""
        doc = self._make_doc([{"item_code": ITEM, "uom": "Nos", "conversion_factor": 2.5}])
        ensure_conversion_factor(doc)
        self.assertEqual(doc.items[0].conversion_factor, 2.5)

    def test_multiple_rows_each_filled_independently(self):
        doc = self._make_doc([
            {"item_code": ITEM, "uom": "Nos", "conversion_factor": None},
            {"item_code": ITEM, "uom": "Nos", "conversion_factor": 3},
            {"item_code": ITEM, "uom": "Nos", "conversion_factor": 0},
        ])
        ensure_conversion_factor(doc)
        self.assertEqual([r.conversion_factor for r in doc.items], [1.0, 3, 1.0])

    def test_empty_items_does_not_raise(self):
        doc = self._make_doc([])
        ensure_conversion_factor(doc)  # must not raise

    def test_row_without_item_code_is_skipped(self):
        """A blank item_code has nothing to resolve a factor against —
        must not raise trying to look one up."""
        doc = self._make_doc([{"item_code": None, "uom": "Nos", "conversion_factor": None}])
        ensure_conversion_factor(doc)  # must not raise
        self.assertIsNone(doc.items[0].conversion_factor)

    def test_method_arg_ignored(self):
        doc = self._make_doc([{"item_code": ITEM, "uom": "Nos", "conversion_factor": None}])
        ensure_conversion_factor(doc, method="validate")
        self.assertEqual(doc.items[0].conversion_factor, 1.0)


class TestWiredIntoCalculateDeliveryNoteTotals(FrappeTestCase):

    def setUp(self):
        if not _item_present():
            self.skipTest("Bench lacks Item 58D/8F this suite is built on.")

    def test_calculate_delivery_note_totals_calls_ensure_conversion_factor(self):
        from mhr.utilis import calculate_delivery_note_totals

        doc = MagicMock()
        doc.items = [frappe._dict(item_code=ITEM, uom="Nos", conversion_factor=None, custom_cone=6, qty=10)]
        doc.total_qty = 0
        calculate_delivery_note_totals(doc)
        self.assertEqual(doc.items[0].conversion_factor, 1.0)


class TestRealDocumentFetchBatchesShapedRow(FrappeTestCase):
    """A row built exactly the way the "Fetch Batches" script builds one
    (uom set, conversion_factor absent) must not block a real submit."""

    CUSTOMER = "Shree Ram Sevak Silk Mills"
    WH = "Finished Goods - MC"
    COMPANY = "Meher Creations"

    def _pick_batch(self):
        rows = frappe.db.sql("""
            SELECT b.name FROM `tabBatch` b
            JOIN (SELECT sbe.batch_no, SUM(sbe.qty) bal FROM `tabSerial and Batch Entry` sbe
                  JOIN `tabSerial and Batch Bundle` sbb ON sbb.name = sbe.parent
                  WHERE sbb.warehouse = %s AND sbb.docstatus = 1 AND sbb.is_cancelled = 0
                    AND sbb.type_of_transaction IN ('Inward', 'Outward')
                  GROUP BY sbe.batch_no HAVING bal >= 1) s ON s.batch_no = b.name
            WHERE b.item = %s AND b.custom_transaction_type = 'VFY' AND b.disabled = 0
            ORDER BY b.name LIMIT 1""", (self.WH, ITEM))
        return rows[0][0] if rows else None

    def setUp(self):
        self.batch = self._pick_batch()
        if not (frappe.db.exists("Customer", self.CUSTOMER) and self.batch):
            self.skipTest("Bench lacks the VFY masters this suite is built on.")
        self.dn = None

    def tearDown(self):
        if self.dn and self.dn.name and frappe.db.exists("Delivery Note", self.dn.name):
            doc = frappe.get_doc("Delivery Note", self.dn.name)
            if doc.docstatus == 1:
                doc.cancel()
            frappe.delete_doc("Delivery Note", self.dn.name, force=1, ignore_permissions=True)

    def test_submit_succeeds_with_a_fetch_batches_shaped_row(self):
        self.dn = frappe.new_doc("Delivery Note")
        self.dn.update({
            "customer": self.CUSTOMER, "company": self.COMPANY, "transaction_type": "VFY",
            "posting_date": frappe.utils.today(), "set_posting_time": 1, "set_warehouse": self.WH,
            "custom_sales_person": "Jayendrabhai", "custom_batch": self.batch,
            "selling_price_list": "Standard Selling", "currency": "INR",
        })
        # Mirrors the Client Script's frm.add_child() call exactly: uom set,
        # conversion_factor absent — the shape that used to block submit.
        self.dn.append("items", {
            "item_code": ITEM, "qty": 1, "batch_no": self.batch, "uom": "Nos",
            "rate": 100, "warehouse": self.WH, "use_serial_batch_fields": 1,
        })
        self.dn.insert(ignore_permissions=True)
        self.dn.submit()  # must not raise MandatoryError on conversion_factor
        self.assertEqual(self.dn.docstatus, 1)
