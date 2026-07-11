"""MI1-I63 reopen (Raj 2026-06-29): DN Items created before the GW
propagation fix stayed at custom_gross_weight = 0 in the DB even
though the linked Batch had a positive Gross Weight. Fetch_from
only fires on interactive batch_no change, and validate-time
backfill only runs when the doc is next saved. Historical rows
remained stale.

Fix: one-shot heal patch that SQL-updates DN Item.custom_gross_weight
from Batch.custom_gross_weight for every non-cancelled DN Item row
where GW=0 and the linked Batch has GW>0.
"""
import inspect
import os

import frappe
from frappe.tests.utils import FrappeTestCase


class TestHealPatchRegistered(FrappeTestCase):

    def test_patch_in_patches_txt(self):
        path = os.path.join(frappe.get_app_path("mhr"), "patches.txt")
        body = open(path).read()
        self.assertIn(
            "mhr.patches.v1_0.heal_dn_item_gross_weight_from_batch",
            body,
            "Heal patch must be registered in patches.txt.",
        )

    def test_patch_module_loadable(self):
        from mhr.patches.v1_0 import heal_dn_item_gross_weight_from_batch as p
        self.assertTrue(callable(getattr(p, "execute", None)),
            "Heal patch must expose an execute() function.")

    def test_patch_guards_and_writes_correctly(self):
        from mhr.patches.v1_0 import heal_dn_item_gross_weight_from_batch as p
        src = inspect.getsource(p)
        # Chunked to be safe on prod DN Item scale.
        self.assertIn("CHUNK_SIZE", src,
            "Patch must chunk to stay safe on prod DN Item scale.")
        # Non-cancelled only.
        self.assertIn("dn.docstatus < 2", src,
            "Patch must skip cancelled DNs.")
        # Guards against clobbering manual overrides.
        self.assertIn(
            "dni.custom_gross_weight IS NULL OR dni.custom_gross_weight = 0",
            src,
            "Patch must only touch DN Item rows with GW=0/NULL — do not "
            "clobber manual overrides.",
        )
        # Requires source to have GW.
        self.assertIn(
            "b.custom_gross_weight > 0", src,
            "Patch must only run when the source Batch has GW>0.",
        )
        # update_modified=False so heal doesn't bump timestamps.
        self.assertIn("update_modified=False", src,
            "Patch must set update_modified=False so healed rows don't "
            "bump their timestamps.")


class TestGoingForwardBackfillOnValidate(FrappeTestCase):
    """Regression pin: the going-forward backfill on DN.validate must
    still be in place. Without it, every future re-save loses the
    heal."""

    def test_backfill_wired_on_validate(self):
        import mhr.hooks as h
        dn_validate = h.doc_events.get("Delivery Note", {}).get("validate") or []
        if isinstance(dn_validate, str):
            dn_validate = [dn_validate]
        self.assertIn(
            "mhr.utilis.calculate_delivery_note_totals",
            dn_validate,
            "DN.validate must call calculate_delivery_note_totals — that's "
            "what runs backfill_dn_item_gross_weight going forward.",
        )

    def test_calculate_totals_calls_backfill(self):
        from mhr import utilis
        src = inspect.getsource(utilis.calculate_delivery_note_totals)
        self.assertIn("backfill_dn_item_gross_weight", src,
            "calculate_delivery_note_totals must invoke "
            "backfill_dn_item_gross_weight — that's the going-forward "
            "propagation.")

    def test_backfill_respects_manual_override(self):
        from mhr import utilis
        src = inspect.getsource(utilis.backfill_dn_item_gross_weight)
        self.assertIn(
            "flt(i.get(\"custom_gross_weight\") or 0) > 0", src,
            "backfill_dn_item_gross_weight must skip rows where "
            "custom_gross_weight is already > 0 (manual override).",
        )
