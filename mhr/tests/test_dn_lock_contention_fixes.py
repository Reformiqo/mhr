import importlib
import os

import frappe
from frappe.core.doctype.scheduled_job_type.scheduled_job_type import sync_jobs
from frappe.tests import IntegrationTestCase

PATCHES = os.path.join(frappe.get_app_path("mhr"), "patches.txt")
PATCH_MODULE = "mhr.patches.v1_0.add_serial_batch_bundle_voucher_index"
REMOVED_JOBS = ("mhr.batch.enqueue_recalculate_batch_qty", "mhr.utilis.enqueue_cancel_receipts")


def _patch():
    return importlib.import_module(PATCH_MODULE)


class TestRemovedSchedulerJobs(IntegrationTestCase):
    def test_migrate_sync_leaves_no_scheduled_job_for_removed_methods(self):
        sync_jobs()
        for method in REMOVED_JOBS:
            self.assertFalse(
                frappe.db.exists("Scheduled Job Type", {"method": method}),
                f"{method} is still scheduled after sync_jobs()",
            )

    def test_removed_whitelisted_endpoints_are_gone(self):
        batch = importlib.import_module("mhr.batch")
        utilis = importlib.import_module("mhr.utilis")
        for mod, name in (
            (batch, "recalculate_batch_qty"),
            (batch, "enqueue_recalculate_batch_qty"),
            (utilis, "cancel_receipts"),
            (utilis, "enqueue_cancel_receipts"),
        ):
            self.assertFalse(hasattr(mod, name), f"{mod.__name__}.{name} is still callable over HTTP")

    def test_bounded_recalculation_still_available(self):
        batch = importlib.import_module("mhr.batch")
        self.assertTrue(callable(batch.recalculate_selected_batches))
        self.assertTrue(callable(batch.get_batch_qty))


class TestSerialBatchBundleVoucherIndex(IntegrationTestCase):
    def test_registered_after_the_entry_table_voucher_index(self):
        with open(PATCHES) as f:
            lines = [l.strip().split()[0] for l in f if l.strip() and not l.startswith("#")]
        self.assertIn(PATCH_MODULE, lines)
        self.assertGreater(
            lines.index(PATCH_MODULE),
            lines.index("mhr.patches.v1_0.add_serial_batch_entry_voucher_index"),
        )

    def test_execute_is_idempotent_and_indexes_exact_columns(self):
        patch = _patch()
        patch.execute()
        patch.execute()
        self.assertTrue(patch.index_exists())
        cols = frappe.db.sql(
            """SELECT GROUP_CONCAT(column_name ORDER BY seq_in_index)
               FROM information_schema.statistics
               WHERE table_schema = DATABASE() AND table_name = %s AND index_name = %s""",
            (patch.TABLE, patch.INDEX_NAME),
        )[0][0]
        self.assertEqual(cols, ",".join(patch.COLUMNS))

    def test_cancel_lookup_uses_the_index_instead_of_scanning_all_dn_bundles(self):
        patch = _patch()
        patch.execute()
        plan = frappe.db.sql(
            """EXPLAIN SELECT name FROM `tabSerial and Batch Bundle`
               WHERE voucher_no = %s AND voucher_type = %s""",
            ("__sbb_voucher_index_probe__", "Delivery Note"),
            as_dict=True,
        )[0]
        self.assertEqual(plan.get("key"), patch.INDEX_NAME)
        self.assertLess(int(plan.get("rows") or 0), 1000)
