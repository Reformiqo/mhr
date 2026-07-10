"""MI1-I63 (reopen 2026-06-29) — Batch master inherits Gross Weight
from Container Inward.

Raj 2026-06-29: 'in transaction type HTY, in the Container (Inward),
the Gross Weight is already available and saved against each batch.
However, while creating the Delivery Note, the Gross Weight field is
showing 0 instead of fetching the value from the corresponding
Container/Batch. The same issue exists while generating the Barcode.'

Root cause: Container.create_batches (and its duplicate in
mhr.utilis.create_batches) copied every field from the Batch Items
child row onto the new Batch master EXCEPT custom_gross_weight.
tabBatch.custom_gross_weight stayed at its default 0, which is what
the DN Item's fetch_from + the HTY 6-up barcode renderer both read.

Fix: propagate custom_gross_weight in both create_batches paths + a
backfill patch for existing Batches.
"""
import inspect
import os

import frappe
from frappe.tests.utils import FrappeTestCase


class TestContainerCreateBatchesCopiesGrossWeight(FrappeTestCase):

    def test_container_method_copies_gross_weight(self):
        from mhr.mhr.doctype.container.container import Container
        src = inspect.getsource(Container.create_batches)
        self.assertIn(
            "batch_doc.custom_gross_weight", src,
            "Container.create_batches must copy custom_gross_weight "
            "from the Batch Items row onto the new Batch master.",
        )
        self.assertIn(
            "batch.get(\"custom_gross_weight\")", src,
            "Must read the value from the Batch Items row via "
            ".get('custom_gross_weight').",
        )

    def test_utilis_create_batches_copies_gross_weight(self):
        from mhr import utilis
        src = inspect.getsource(utilis.create_batches)
        self.assertIn(
            "batch_doc.custom_gross_weight", src,
            "mhr.utilis.create_batches (the duplicate path used by "
            "programmatic Container creation) must also copy "
            "custom_gross_weight.",
        )
        self.assertIn(
            "batch.get(\"custom_gross_weight\")", src,
        )


class TestBackfillPatch(FrappeTestCase):

    def test_patch_registered(self):
        path = os.path.join(frappe.get_app_path("mhr"), "patches.txt")
        body = open(path).read()
        self.assertIn(
            "mhr.patches.v1_0.backfill_batch_gross_weight_from_batch_items",
            body,
            "Backfill patch must be in patches.txt.",
        )

    def test_patch_module_loadable(self):
        from mhr.patches.v1_0 import backfill_batch_gross_weight_from_batch_items as p
        self.assertTrue(callable(getattr(p, "execute", None)))

    def test_patch_chunks_and_gates(self):
        from mhr.patches.v1_0 import backfill_batch_gross_weight_from_batch_items as p
        src = inspect.getsource(p)
        self.assertIn("CHUNK_SIZE", src,
            "Patch must chunk to avoid lock-wait timeouts on 295k rows.")
        self.assertIn("bi.custom_gross_weight > 0", src,
            "Only backfill Batches whose Batch Items row has a "
            "positive value.")
        self.assertIn("b.custom_gross_weight IS NULL OR b.custom_gross_weight = 0", src,
            "Idempotent — only touch Batches whose master GW is 0.")


class TestPriorFetchStillWorks(FrappeTestCase):
    """The 2026-06-23 fix (fetch_from on the DN Item Custom Field +
    server-side backfill helper) must still be in place — it's the
    other half of the chain."""

    def test_dn_item_fetch_from_intact(self):
        fetch_from = frappe.db.get_value(
            "Custom Field", "Delivery Note Item-custom_gross_weight",
            "fetch_from",
        )
        self.assertEqual(fetch_from, "batch_no.custom_gross_weight",
            "DN Item fetch_from must still be batch_no.custom_gross_weight "
            "(2026-06-23 fix).")

    def test_backfill_helper_still_called(self):
        from mhr import utilis
        src = inspect.getsource(utilis.calculate_delivery_note_totals)
        self.assertIn("backfill_dn_item_gross_weight(doc)", src,
            "DN backfill helper must still fire from validate.")


class TestHtyBarcodeRendersGrossWeight(FrappeTestCase):
    """Sanity pin on the HTY 6-up barcode template: it must read
    doc.custom_gross_weight (the Batch's field). If we ever renamed
    or restructured that, the barcode would silently print blank."""

    def test_renderer_reads_custom_gross_weight(self):
        from mhr.utilis import render_hty_6up_pdf
        src = inspect.getsource(render_hty_6up_pdf)
        self.assertIn('doc.get("custom_gross_weight")', src,
            "Renderer must read the Batch's custom_gross_weight.")

    def test_label_template_has_gross_wt_row(self):
        from mhr.utilis import HTY_LABEL_HTML
        self.assertIn("Gross Wt", HTY_LABEL_HTML,
            "Template must include the Gross Wt row.")
        self.assertIn("{{ gross_wt_str }}", HTY_LABEL_HTML,
            "Template must use the gross_wt_str context var.")
