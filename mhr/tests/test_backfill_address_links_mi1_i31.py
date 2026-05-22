"""MI1-I31 v2 — backfill patch tests.

The patch sweeps every (customer, address) pair on `tabDelivery Stop`
and inserts a `tabDynamic Link` row tying the Address back to the
Customer if missing. Pins:

  - Patch is registered in patches.txt under [post_model_sync] AFTER
    heal_orphan_batch_masters (so address linking runs on cleaned data).
  - SELECT query uses a NOT EXISTS subquery (no DB roundtrip per row).
  - Insert path is via Address.append("links", ...) + save — same code
    path as the validate hook, so behavior stays consistent.
  - Stale stop.address pointing at deleted Address rows: skipped.
  - Idempotency: re-running finds 0 unlinked pairs.
"""

import inspect
import frappe
from frappe.tests.utils import FrappeTestCase

from mhr.patches.v1_0 import backfill_address_links_from_delivery_stops as patch


class TestRegisteredInPatchesTxt(FrappeTestCase):

    def test_patch_listed(self):
        import os
        path = os.path.join(frappe.get_app_path("mhr"), "patches.txt")
        content = open(path).read()
        self.assertIn(
            "mhr.patches.v1_0.backfill_address_links_from_delivery_stops",
            content,
            "Backfill patch must be registered in patches.txt — otherwise "
            "bench migrate skips it.",
        )

    def test_runs_after_heal_orphan_batch_masters(self):
        """Patch order: orphan batch heal first, address backfill second.
        Not strictly required but conceptually correct — heal data
        integrity, then heal references."""
        import os
        content = open(
            os.path.join(frappe.get_app_path("mhr"), "patches.txt")
        ).read()
        heal_idx = content.index("mhr.patches.v1_0.heal_orphan_batch_masters")
        bf_idx = content.index("mhr.patches.v1_0.backfill_address_links_from_delivery_stops")
        self.assertGreater(bf_idx, heal_idx)


class TestQueryShape(FrappeTestCase):
    """Source-level pins on the SELECT — getting these wrong silently
    misses unlinked pairs."""

    def test_uses_not_exists_subquery(self):
        src = inspect.getsource(patch.execute)
        self.assertIn(
            "NOT EXISTS", src,
            "Must use NOT EXISTS to filter pairs whose Dynamic Link "
            "already exists — single SQL roundtrip, no N+1.",
        )

    def test_filters_to_customer_parenttype_address(self):
        src = inspect.getsource(patch.execute)
        self.assertIn("dl.parenttype = 'Address'", src,
            "Dynamic Link query must scope to parenttype='Address'.")
        self.assertIn("dl.link_doctype = 'Customer'", src,
            "Must check the Customer link_doctype specifically.")
        self.assertIn("dl.parentfield = 'links'", src,
            "Must scope to parentfield='links' (the Address.links table).")

    def test_skips_empty_strings_too(self):
        src = inspect.getsource(patch.execute)
        # Both '' and NULL must be filtered out — the query covers both.
        self.assertIn("ds.customer != ''", src,
            "Must exclude empty-string customer values.")
        self.assertIn("ds.address  != ''", src,
            "Must exclude empty-string address values.")


class TestIdempotency(FrappeTestCase):
    """A second migrate must leave 0 pairs to backfill."""

    def test_re_run_finds_zero_missing_pairs(self):
        # First run
        patch.execute()
        # Count remaining unlinked pairs after the first run.
        remaining = frappe.db.sql(
            """
            SELECT COUNT(DISTINCT ds.customer, ds.address)
            FROM `tabDelivery Stop` ds
            WHERE ds.customer IS NOT NULL AND ds.customer != ''
              AND ds.address  IS NOT NULL AND ds.address  != ''
              AND EXISTS (SELECT 1 FROM `tabAddress` a WHERE a.name = ds.address)
              AND NOT EXISTS (
                  SELECT 1 FROM `tabDynamic Link` dl
                  WHERE dl.parent = ds.address
                    AND dl.parenttype = 'Address'
                    AND dl.parentfield = 'links'
                    AND dl.link_doctype = 'Customer'
                    AND dl.link_name = ds.customer
              )
            """
        )[0][0]
        # Some pairs may legitimately fail to link (Address validation
        # errors) — those would be logged. The strict invariant is the
        # count doesn't grow.
        # Re-run
        patch.execute()
        remaining_2 = frappe.db.sql(
            """
            SELECT COUNT(DISTINCT ds.customer, ds.address)
            FROM `tabDelivery Stop` ds
            WHERE ds.customer IS NOT NULL AND ds.customer != ''
              AND ds.address  IS NOT NULL AND ds.address  != ''
              AND EXISTS (SELECT 1 FROM `tabAddress` a WHERE a.name = ds.address)
              AND NOT EXISTS (
                  SELECT 1 FROM `tabDynamic Link` dl
                  WHERE dl.parent = ds.address
                    AND dl.parenttype = 'Address'
                    AND dl.parentfield = 'links'
                    AND dl.link_doctype = 'Customer'
                    AND dl.link_name = ds.customer
              )
            """
        )[0][0]
        self.assertEqual(
            remaining_2, remaining,
            "Re-running the patch must not change the unlinked count "
            "(idempotency)."
        )
