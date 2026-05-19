"""MI1-I41 — heal_orphan_batch_masters patch tests.

Background:
  - Pre-MI1-I36-F2, `Container.on_cancel` ran
    `DELETE FROM tabBatch WHERE name = %s` without checking for
    other Containers referencing the same batch_id.
  - Cancelling a duplicate Container wiped Batch masters the
    SURVIVING Submitted Container still needed.
  - Result: `tabBatch Items` child rows on a Submitted Container point
    at non-existent Batch masters → Container Report skips the lot,
    Stock Balance Report misses it, batch form opens blank.

Raj reported the symptom three times (MCJC-1361, MCJC-1566, MCJC-1538).
Each time we healed manually via console. This patch heals globally
via `bench migrate` AND any other un-noticed corruption from the same
era — one-shot, idempotent.

Tests pin:
  - Patch is registered in patches.txt under [post_model_sync] AFTER
    rename_normal_to_vfy.
  - The orphan-finder query joins tabContainer with `docstatus=1` so
    Cancelled containers don't seed re-creation.
  - Batch creation strips whitespace from the item field (legacy data
    had ` 30S ECOSHINE RING SPUN YARN` with leading space).
  - Skips orphans whose Item doesn't exist (logs an error, never raises).
  - batch_qty is pinned from the child row's qty (mirror, not SLE replay).
  - Re-run is a no-op (idempotency).
"""

import inspect
import frappe
from frappe.tests.utils import FrappeTestCase

from mhr.patches.v1_0 import heal_orphan_batch_masters as patch


class TestRegisteredInPatchesTxt(FrappeTestCase):

    def test_patch_listed(self):
        import os
        path = os.path.join(frappe.get_app_path("mhr"), "patches.txt")
        content = open(path).read()
        self.assertIn(
            "mhr.patches.v1_0.heal_orphan_batch_masters",
            content,
            "Heal patch must be registered in patches.txt — otherwise bench migrate "
            "skips it.",
        )

    def test_runs_after_rename_patch(self):
        """The heal depends on data being in its final transaction_type
        state (rename_normal_to_vfy already ran). Run order matters."""
        import os
        content = open(
            os.path.join(frappe.get_app_path("mhr"), "patches.txt")
        ).read()
        rename_idx = content.index("mhr.patches.v1_0.rename_normal_to_vfy")
        heal_idx = content.index("mhr.patches.v1_0.heal_orphan_batch_masters")
        self.assertGreater(
            heal_idx, rename_idx,
            "heal must run AFTER rename — otherwise it operates on stale data.",
        )


class TestOrphanFinderQuery(FrappeTestCase):

    def test_filters_to_submitted_containers_only(self):
        src = inspect.getsource(patch.execute)
        self.assertIn(
            "c.docstatus = 1", src,
            "Orphan-finder MUST filter to Submitted Containers — a Cancelled "
            "Container's child rows are not authoritative.",
        )

    def test_skips_empty_batch_ids(self):
        src = inspect.getsource(patch.execute)
        self.assertIn(
            "bi.batch_id IS NOT NULL", src,
            "Empty / NULL batch_id rows must be excluded — they're placeholder "
            "rows from incomplete entries, not orphan Batches.",
        )
        self.assertIn(
            "bi.batch_id != ''", src,
            "Same — empty-string batch_ids must be filtered out too.",
        )

    def test_query_uses_not_exists_against_tabbatch(self):
        src = inspect.getsource(patch.execute)
        self.assertIn(
            "NOT EXISTS (\n              SELECT 1 FROM `tabBatch` b WHERE b.name = bi.batch_id\n          )",
            src,
            "Orphan-detection must use NOT EXISTS against tabBatch — the cheapest "
            "way to find missing Batch masters at scale.",
        )


class TestItemStripAndGuard(FrappeTestCase):

    def test_item_stripped(self):
        src = inspect.getsource(patch.execute)
        self.assertIn(
            '(row.child_item or "").strip()', src,
            "Item code must be stripped of whitespace — legacy Batch Items rows "
            "have leading-space item codes that don't match the Item master.",
        )

    def test_skips_missing_item(self):
        src = inspect.getsource(patch.execute)
        self.assertIn(
            'frappe.db.exists("Item", item_code)', src,
            "Patch must verify the Item exists before creating the Batch — "
            "otherwise it crashes with 'cannot unpack non-iterable NoneType' "
            "deep in Frappe's Link validation.",
        )

    def test_batch_qty_pinned_from_child(self):
        src = inspect.getsource(patch.execute)
        self.assertIn(
            'frappe.db.set_value(\n                "Batch", row.batch_id, "batch_qty", flt(row.child_qty)\n            )',
            src,
            "batch_qty must be pinned from the child row's qty — SLE replay "
            "isn't reliable for orphan heals (the cancelled-Container's SLE "
            "rows are cancelled, so a recompute would set batch_qty=0).",
        )


class TestIdempotency(FrappeTestCase):
    """The heal must be safely re-runnable. bench migrate re-runs
    patches on every deploy if they're not in tabPatch Log."""

    def test_re_run_finds_zero_orphans(self):
        # First run — heal whatever we have.
        patch.execute()
        # Second run — should find 0 (everything either healed or
        # skipped with logged error).
        before = frappe.db.sql("""
            SELECT COUNT(*) FROM `tabBatch Items` bi
            JOIN `tabContainer` c ON c.name = bi.parent
            WHERE bi.parenttype='Container' AND c.docstatus=1
              AND bi.batch_id IS NOT NULL AND bi.batch_id != ''
              AND NOT EXISTS (SELECT 1 FROM `tabBatch` b WHERE b.name = bi.batch_id)
              AND EXISTS (SELECT 1 FROM `tabItem` i WHERE i.name = TRIM(bi.item))
        """)[0][0]
        patch.execute()
        after = frappe.db.sql("""
            SELECT COUNT(*) FROM `tabBatch Items` bi
            JOIN `tabContainer` c ON c.name = bi.parent
            WHERE bi.parenttype='Container' AND c.docstatus=1
              AND bi.batch_id IS NOT NULL AND bi.batch_id != ''
              AND NOT EXISTS (SELECT 1 FROM `tabBatch` b WHERE b.name = bi.batch_id)
              AND EXISTS (SELECT 1 FROM `tabItem` i WHERE i.name = TRIM(bi.item))
        """)[0][0]
        # The re-run must not create duplicates and must not crash on
        # the same data. Strict invariant: after second run, no
        # healable orphans remain.
        self.assertEqual(after, 0,
            "After two runs, no healable orphans should remain. "
            f"Found {after} (before={before}).")
