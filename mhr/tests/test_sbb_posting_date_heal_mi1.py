"""MI1 (Raj 2026-07-13) — DN submit was throwing 'Batch has negative
stock' errors for batches whose Serial and Batch Bundle inward entry
had NULL posting_date.

erpnext's batch availability query in
erpnext.stock.serial_batch_bundle.BatchNoValuation.get_batch_no_ledgers()
uses:
    CombineDatetime(sbb.posting_date, sbb.posting_time) < DN.posting_date
When posting_date is NULL, that comparison evaluates to NULL and the
row is silently excluded. available_qty for the batch falls to 0 and
the outward deduction becomes a negative-stock violation.

Fix:
  1. Heal patch backfills posting_date + posting_time on every submitted
     SBB with NULL posting_date, from its linked voucher's posting date.
  2. Container.create_serial_and_batch_bundle() now sets posting_date
     from Container.posting_date at SBB creation time so going-forward
     SBBs never have NULL posting_date.
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
            "mhr.patches.v1_0.heal_sbb_posting_date_from_voucher",
            body,
            "Heal patch must be registered in patches.txt.",
        )

    def test_patch_module_loadable(self):
        from mhr.patches.v1_0 import heal_sbb_posting_date_from_voucher as p
        self.assertTrue(callable(getattr(p, "execute", None)))

    def test_patch_covers_all_three_voucher_types(self):
        from mhr.patches.v1_0 import heal_sbb_posting_date_from_voucher as p
        src = inspect.getsource(p)
        for vt in ("Purchase Receipt", "Delivery Note", "Stock Entry"):
            self.assertIn(f'"{vt}"', src,
                f"Heal patch must cover voucher_type={vt!r} — DN/SE SBBs "
                "also had NULL posting_date and would still block queries.")

    def test_patch_only_touches_null_and_existing_vouchers(self):
        from mhr.patches.v1_0 import heal_sbb_posting_date_from_voucher as p
        src = inspect.getsource(p)
        self.assertIn(
            "sbb.posting_date IS NULL",
            src,
            "Patch must only touch SBBs whose posting_date is NULL — "
            "don't clobber correctly-set rows.",
        )
        self.assertIn(
            "v.posting_date IS NOT NULL",
            src,
            "Patch must skip SBBs whose voucher has no posting_date "
            "(cancelled / weird rows) — safer to leave them alone.",
        )
        self.assertIn(
            "update_modified=False",
            src,
            "Heal must not bump SBB modified timestamps.",
        )


class TestGoingForwardSbbHasPostingDate(FrappeTestCase):
    """Regression pin: Container.create_serial_and_batch_bundle sets
    posting_date/posting_time on the SBB before saving. Without this,
    every new Container inward reintroduces the NULL-posting bug."""

    def test_container_sbb_creation_sets_posting_date(self):
        from mhr.mhr.doctype.container.container import Container
        src = inspect.getsource(Container.create_serial_and_batch_bundle)
        self.assertIn(
            "sb_bundle.posting_date = self.posting_date",
            src,
            "Container.create_serial_and_batch_bundle must set SBB "
            "posting_date from Container.posting_date before save — "
            "otherwise erpnext's time-conditioned batch availability "
            "query silently drops the inward row.",
        )
        self.assertIn(
            "sb_bundle.posting_time",
            src,
            "Container.create_serial_and_batch_bundle must also set SBB "
            "posting_time — the CombineDatetime comparison uses both.",
        )


class TestNoNullPostingSbbsInDb(FrappeTestCase):
    """After the heal patch runs, no submitted SBB should carry NULL
    posting_date. If this regresses, DN submits will randomly throw
    negative stock errors again."""

    def test_zero_null_posting_submitted_sbbs(self):
        count = frappe.db.count(
            "Serial and Batch Bundle",
            filters={
                "docstatus": 1,
                "is_cancelled": 0,
                "posting_date": ["is", "not set"],
            },
        )
        self.assertEqual(
            count, 0,
            f"There must be no submitted SBBs with NULL posting_date. "
            f"Got {count}. Run the heal patch: "
            "`bench --site <site> execute "
            "mhr.patches.v1_0.heal_sbb_posting_date_from_voucher.execute`.",
        )
