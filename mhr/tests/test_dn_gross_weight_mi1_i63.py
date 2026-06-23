"""MI1-I63 (2026-06-23) — Gross Weight must reach the Delivery Note.

Raj's bug: Container Inward saves custom_gross_weight on the Batch,
but when a Delivery Note row references that Batch via batch_no, the
DN Item's custom_gross_weight stays empty. Two-layer fix:

  1. Custom Field fetch_from = 'batch_no.custom_gross_weight' so the
     form auto-fills on batch_no change.
  2. Server-side backfill in calculate_delivery_note_totals (DN.validate)
     so programmatic creates (SO mapper, imports, bulk scripts) also
     get the value.

The backfill uses fetch_if_empty semantics — it never clobbers a
manually-entered gross_weight.
"""

import inspect
import re

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt


class TestCustomFieldFetchFrom(FrappeTestCase):

    def test_fetch_from_set(self):
        fetch_from = frappe.db.get_value(
            "Custom Field", "Delivery Note Item-custom_gross_weight",
            "fetch_from",
        )
        self.assertEqual(
            fetch_from, "batch_no.custom_gross_weight",
            "DN Item custom_gross_weight must fetch from batch_no.custom_gross_weight.",
        )

    def test_fetch_if_empty(self):
        fetch_if_empty = frappe.db.get_value(
            "Custom Field", "Delivery Note Item-custom_gross_weight",
            "fetch_if_empty",
        )
        self.assertTrue(fetch_if_empty,
            "fetch_if_empty must be 1 so manually-entered values aren't clobbered.")


class TestBackfillFunctionShape(FrappeTestCase):
    """Source-level pin on the backfill helper."""

    def test_helper_exists(self):
        from mhr import utilis
        self.assertTrue(callable(getattr(utilis, "backfill_dn_item_gross_weight", None)),
            "mhr.utilis.backfill_dn_item_gross_weight must exist.")

    def test_helper_called_from_calculate_totals(self):
        from mhr import utilis
        src = inspect.getsource(utilis.calculate_delivery_note_totals)
        self.assertIn("backfill_dn_item_gross_weight(doc)", src,
            "calculate_delivery_note_totals (the DN.validate hook) must "
            "call backfill_dn_item_gross_weight so programmatic creates "
            "still get the value.")

    def test_helper_skips_rows_with_value(self):
        from mhr import utilis
        src = inspect.getsource(utilis.backfill_dn_item_gross_weight)
        # Skip rows whose custom_gross_weight is already > 0 (manual override).
        self.assertIn("custom_gross_weight", src)
        self.assertIn("flt(", src,
            "Use flt() for the numeric comparison (never compare floats directly).")

    def test_helper_uses_frappe_get_all_batched(self):
        from mhr import utilis
        src = inspect.getsource(utilis.backfill_dn_item_gross_weight)
        self.assertIn('frappe.get_all(\n        "Batch"', src,
            "Helper must use frappe.get_all('Batch', ...) to fetch in ONE query "
            "(not per-row get_value — that's O(N) queries on a 245-batch DN).")


class TestBackfillBehaviour(FrappeTestCase):
    """End-to-end: feed the backfill a fake DN doc with Batch refs and
    assert the gross weight gets copied."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Find a Batch with custom_gross_weight > 0 if any; otherwise skip
        # behavioural tests but keep source-level pins green.
        cls.batch_with_gw = frappe.db.sql(
            "SELECT name, custom_gross_weight FROM `tabBatch` "
            "WHERE custom_gross_weight > 0 LIMIT 1", as_dict=True)

    def setUp(self):
        if not self.batch_with_gw:
            # Synthesise: pick any batch and stamp a gross weight on it
            # for the duration of the test.
            row = frappe.db.sql(
                "SELECT name FROM `tabBatch` LIMIT 1", as_dict=True)
            if not row:
                self.skipTest("No Batch rows in this site — cannot test.")
            self.batch_name = row[0].name
            self.original_gw = frappe.db.get_value(
                "Batch", self.batch_name, "custom_gross_weight") or 0
            frappe.db.set_value("Batch", self.batch_name, "custom_gross_weight", 42.5,
                                update_modified=False)
            frappe.db.commit()
            self._restore_needed = True
        else:
            self.batch_name = self.batch_with_gw[0].name
            self._restore_needed = False

    def tearDown(self):
        if getattr(self, "_restore_needed", False):
            frappe.db.set_value("Batch", self.batch_name, "custom_gross_weight",
                                self.original_gw, update_modified=False)
            frappe.db.commit()

    def _fake_doc(self, rows):
        from types import SimpleNamespace
        return SimpleNamespace(
            items=[SimpleNamespace(**r) for r in rows],
            get=lambda k, default=None: getattr(SimpleNamespace(items=[SimpleNamespace(**r) for r in rows]), k, default),
        )

    def test_backfill_populates_empty_row(self):
        """A DN Item with batch_no set + custom_gross_weight=0 must get
        the Batch's custom_gross_weight copied in."""
        from mhr.utilis import backfill_dn_item_gross_weight
        class Item:
            def __init__(self, **kw):
                self.__dict__.update(kw)
                # default-zero
                self.custom_gross_weight = kw.get("custom_gross_weight", 0)
            def get(self, k, default=None):
                return getattr(self, k, default)
        class Doc:
            def __init__(self, items):
                self.items = items
            def get(self, k, default=None):
                return getattr(self, k, default)
        item = Item(batch_no=self.batch_name)
        doc = Doc([item])
        backfill_dn_item_gross_weight(doc)
        expected = flt(frappe.db.get_value("Batch", self.batch_name, "custom_gross_weight"))
        self.assertEqual(flt(item.custom_gross_weight), expected,
            f"Backfill must copy Batch.custom_gross_weight ({expected}) onto the DN row.")

    def test_backfill_skips_row_with_existing_value(self):
        """A DN Item with custom_gross_weight already > 0 must NOT be
        overwritten — manual override sticks."""
        from mhr.utilis import backfill_dn_item_gross_weight
        class Item:
            def __init__(self, **kw):
                self.__dict__.update(kw)
            def get(self, k, default=None):
                return getattr(self, k, default)
        class Doc:
            def __init__(self, items):
                self.items = items
            def get(self, k, default=None):
                return getattr(self, k, default)
        item = Item(batch_no=self.batch_name, custom_gross_weight=99.99)
        doc = Doc([item])
        backfill_dn_item_gross_weight(doc)
        self.assertEqual(flt(item.custom_gross_weight), 99.99,
            "Backfill must not clobber a manually-entered gross_weight.")

    def test_backfill_handles_row_without_batch(self):
        """Rows without batch_no must be skipped silently — no crash."""
        from mhr.utilis import backfill_dn_item_gross_weight
        class Item:
            def __init__(self, **kw):
                self.__dict__.update(kw)
                self.custom_gross_weight = kw.get("custom_gross_weight", 0)
            def get(self, k, default=None):
                return getattr(self, k, default)
        class Doc:
            def __init__(self, items):
                self.items = items
            def get(self, k, default=None):
                return getattr(self, k, default)
        item = Item(batch_no=None)
        doc = Doc([item])
        backfill_dn_item_gross_weight(doc)  # must not throw
        self.assertEqual(flt(item.custom_gross_weight), 0)

    def test_backfill_handles_empty_items_list(self):
        from mhr.utilis import backfill_dn_item_gross_weight
        class Doc:
            items = []
            def get(self, k, default=None):
                return getattr(self, k, default)
        backfill_dn_item_gross_weight(Doc())  # must not throw
