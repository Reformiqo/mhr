"""MI1 (Raj 2026-07-10) — Batch.batch_qty + manufacturing_date must
propagate from Container Inward.

Screenshot on MCZFT-01: the HTY Select-Batch popup on Delivery Note
listed rows correctly but the 'Batch Qty' and 'Mfg Date' columns
showed '-' on every row. Same class as MI1-I63 (Gross Weight):
Container.create_batches (and mhr.utilis.create_batches) never
copied qty/manufacturing_date onto the Batch master.

Fix mirrors MI1-I63:
  * Both create_batches paths copy batch.qty → Batch.batch_qty
    and Container.posting_date → Batch.manufacturing_date.
  * Backfill patch for existing rows.
"""
import inspect
import os

import frappe
from frappe.tests.utils import FrappeTestCase


class TestContainerCreateBatchesCopiesQtyAndDate(FrappeTestCase):

    def test_container_method_copies_batch_qty(self):
        from mhr.mhr.doctype.container.container import Container
        src = inspect.getsource(Container.create_batches)
        self.assertIn(
            "batch_doc.batch_qty", src,
            "Container.create_batches must set batch_doc.batch_qty from "
            "the Batch Items row.",
        )
        self.assertRegex(
            src,
            r'batch_doc\.batch_qty\s*=\s*flt\(batch\.get\(["\']qty["\']\)',
            "Must read qty from the Batch Items row.",
        )

    def test_container_method_copies_mfg_date(self):
        from mhr.mhr.doctype.container.container import Container
        src = inspect.getsource(Container.create_batches)
        self.assertIn("batch_doc.manufacturing_date", src,
            "Container.create_batches must set manufacturing_date.")
        self.assertIn("self.posting_date", src,
            "Manufacturing date comes from Container.posting_date.")

    def test_utilis_create_batches_copies_batch_qty(self):
        from mhr import utilis
        src = inspect.getsource(utilis.create_batches)
        self.assertIn("batch_doc.batch_qty", src,
            "mhr.utilis.create_batches must mirror the batch_qty copy.")

    def test_utilis_create_batches_copies_mfg_date(self):
        from mhr import utilis
        src = inspect.getsource(utilis.create_batches)
        self.assertIn("batch_doc.manufacturing_date", src,
            "mhr.utilis.create_batches must mirror the manufacturing_date "
            "copy so both Container-creation paths stay in sync.")
        self.assertIn("container_doc.posting_date", src,
            "Manufacturing date comes from container_doc.posting_date.")


class TestBackfillPatch(FrappeTestCase):

    def test_patch_registered(self):
        path = os.path.join(frappe.get_app_path("mhr"), "patches.txt")
        body = open(path).read()
        self.assertIn(
            "mhr.patches.v1_0.backfill_batch_qty_and_mfg_date",
            body,
            "Backfill patch must be in patches.txt.",
        )

    def test_patch_module_loadable(self):
        from mhr.patches.v1_0 import backfill_batch_qty_and_mfg_date as p
        self.assertTrue(callable(getattr(p, "execute", None)))

    def test_patch_chunks_and_gates(self):
        from mhr.patches.v1_0 import backfill_batch_qty_and_mfg_date as p
        src = inspect.getsource(p)
        self.assertIn("CHUNK_SIZE", src,
            "Patch must chunk to stay safe on the 295k-row tabBatch.")
        # Only touches empty targets — idempotent.
        self.assertIn(
            "b.batch_qty IS NULL OR b.batch_qty = 0", src,
            "Patch must guard so it only touches Batches whose "
            "batch_qty is 0 / NULL.",
        )
        # Joins Container so posting_date is available for manufacturing_date.
        self.assertIn("INNER JOIN `tabContainer`", src,
            "Patch must join tabContainer for posting_date lookup.")


class TestPriorFixesStillPresent(FrappeTestCase):
    """The MI1-I63 fix chain (Gross Weight propagation) must survive
    the layered change on top of it."""

    def test_gross_weight_copy_still_in_container_create_batches(self):
        from mhr.mhr.doctype.container.container import Container
        src = inspect.getsource(Container.create_batches)
        self.assertIn("batch_doc.custom_gross_weight", src,
            "MI1-I63 Gross Weight copy must remain.")

    def test_gross_weight_copy_still_in_utilis_create_batches(self):
        from mhr import utilis
        src = inspect.getsource(utilis.create_batches)
        self.assertIn("batch_doc.custom_gross_weight", src)
