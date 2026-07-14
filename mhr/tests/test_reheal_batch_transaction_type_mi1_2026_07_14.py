"""MI1 (2026-07-14) — re-heal Batch.custom_transaction_type.

Rationale: the original backfill_batch_transaction_type patch ran on
2026-06-24, but by 2026-07-14 there were 354,554 out of 378,409
Batches with NULL custom_transaction_type again — every new batch
created since the first heal. The Batch.validate hook is supposed
to auto-fill the field from the linked Container, but some code paths
(bulk imports, Container.create_batches before the Container is fully
saved) leave batches unresolved.

This patch delegates to the existing backfill so the SQL isn't
forked. If drift recurs, add another dated re-heal patch that also
delegates.
"""
import inspect
import os

import frappe
from frappe.tests.utils import FrappeTestCase


class TestRehealPatchRegistered(FrappeTestCase):

    def test_patch_in_patches_txt(self):
        path = os.path.join(frappe.get_app_path("mhr"), "patches.txt")
        body = open(path).read()
        self.assertIn(
            "mhr.patches.v1_0.reheal_batch_transaction_type_2026_07_14",
            body,
            "Dated re-heal patch must be registered in patches.txt so "
            "bench migrate on prod picks it up.",
        )

    def test_patch_module_loadable(self):
        from mhr.patches.v1_0 import reheal_batch_transaction_type_2026_07_14 as p
        self.assertTrue(callable(getattr(p, "execute", None)))

    def test_patch_delegates_to_original(self):
        """Don't fork the SQL — the re-heal must call the existing
        backfill function directly. That way any future fix to the
        SQL lives in one place."""
        from mhr.patches.v1_0 import reheal_batch_transaction_type_2026_07_14 as p
        src = inspect.getsource(p)
        self.assertIn(
            "from mhr.patches.v1_0.backfill_batch_transaction_type import execute",
            src,
            "Re-heal must import the original backfill's execute — not "
            "duplicate the SQL.",
        )
        self.assertIn(
            "backfill()", src,
            "Re-heal must invoke the imported backfill() function.",
        )


class TestNoNullTransactionTypeInDb(FrappeTestCase):
    """After the re-heal patch runs, no batch should have
    NULL/empty custom_transaction_type. If this ever regresses, add
    another dated re-heal — DON'T just clear this test."""

    def test_zero_null_or_empty_batches(self):
        count = frappe.db.sql(
            """SELECT COUNT(*) FROM `tabBatch`
               WHERE custom_transaction_type IS NULL
                  OR custom_transaction_type = ''"""
        )[0][0]
        self.assertEqual(
            count, 0,
            f"There must be no batches with NULL custom_transaction_type. "
            f"Got {count}. Re-run the heal: `bench --site <site> execute "
            f"mhr.patches.v1_0.backfill_batch_transaction_type.execute` "
            f"and add another dated re-heal patch to patches.txt.",
        )
