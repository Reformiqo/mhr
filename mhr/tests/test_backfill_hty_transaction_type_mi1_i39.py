"""MI1-I39 — Backfill patch tests.

`mhr.patches.v1_0.backfill_hty_transaction_type` sets transaction_type
to 'Normal' on legacy docs (Container, SO, DN, SE, Print Batch, Trip)
where it was NULL/empty. Pinning so a refactor doesn't accidentally
drop a table or weaken the WHERE guard.
"""

import inspect
import frappe
from frappe.tests.utils import FrappeTestCase

from mhr.patches.v1_0 import backfill_hty_transaction_type as patch


EXPECTED_TABLES = {
    "tabContainer",
    "tabSales Order",
    "tabDelivery Note",
    "tabStock Entry",
    "tabPrint Batch",
    "tabDelivery Trip",
}


class TestBackfillPatchListedInPatchesTxt(FrappeTestCase):
    """patches.txt must list the patch under [post_model_sync]."""

    def test_patch_is_registered(self):
        import os
        patches_path = os.path.join(
            frappe.get_app_path("mhr"), "patches.txt"
        )
        with open(patches_path) as f:
            content = f.read()
        self.assertIn(
            "mhr.patches.v1_0.backfill_hty_transaction_type",
            content,
            "Patch must be listed in patches.txt so `bench migrate` runs it.",
        )
        # Must be in the post_model_sync section (not pre_model_sync) —
        # the Custom Fields it backfills aren't present until model sync.
        post_idx = content.index("[post_model_sync]")
        patch_idx = content.index("mhr.patches.v1_0.backfill_hty_transaction_type")
        self.assertGreater(
            patch_idx, post_idx,
            "Backfill patch must run AFTER model sync — the transaction_type "
            "column doesn't exist until the Custom Field fixture imports.",
        )


class TestBackfillPatchTouchesAllSixTables(FrappeTestCase):
    """The patch must hit every DocType that got transaction_type in Phase 1."""

    def test_tables_constant(self):
        self.assertEqual(
            set(patch.TABLES),
            EXPECTED_TABLES,
            "TABLES constant must list exactly the 6 DocTypes that have "
            "transaction_type. A missing table = legacy docs of that type "
            "stay NULL and disappear from Normal filter.",
        )

    def test_each_table_has_an_update_statement(self):
        src = inspect.getsource(patch.execute)
        for table in EXPECTED_TABLES:
            # We don't hard-pin the table name in the source (it's iterated
            # over), but the UPDATE template must reference the iteration var.
            pass
        self.assertIn("UPDATE `{table}`", src,
            "Patch must run an UPDATE per table.")
        self.assertIn(
            "SET transaction_type = 'Normal'", src,
            "Backfill value must be 'Normal' (FRD default).",
        )

    def test_update_is_guarded(self):
        src = inspect.getsource(patch.execute)
        self.assertIn(
            "IFNULL(transaction_type, '') = ''", src,
            "UPDATE must be guarded so already-set rows (Normal or HTY) are "
            "not overwritten. Otherwise re-running the patch wipes user data.",
        )


class TestBackfillPatchIsIdempotent(FrappeTestCase):
    """Running the patch twice must be a no-op the second time."""

    def test_re_run_leaves_no_nulls(self):
        # First run (might already be done by earlier session step).
        patch.execute()
        # Second run — must not raise, must leave 0 NULLs everywhere.
        patch.execute()
        for table in EXPECTED_TABLES:
            if not patch._column_exists(table, "transaction_type"):
                continue  # acceptable on benches without the field
            remaining = frappe.db.sql(
                f"SELECT COUNT(*) FROM `{table}` WHERE IFNULL(transaction_type,'')=''"
            )[0][0]
            self.assertEqual(
                remaining, 0,
                f"After patch, `{table}` must have 0 rows with NULL/empty "
                "transaction_type. Found {remaining}.",
            )


class TestBackfillPatchColumnExistsHelper(FrappeTestCase):
    """_column_exists guards against missing columns (e.g. fresh test bench
    where fixtures haven't run yet)."""

    def test_existing_column_returns_true(self):
        self.assertTrue(patch._column_exists("tabContainer", "name"))

    def test_missing_column_returns_false(self):
        self.assertFalse(patch._column_exists("tabContainer", "__nope__"))
