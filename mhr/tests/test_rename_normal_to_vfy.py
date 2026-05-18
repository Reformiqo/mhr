"""Tests for the Normal → VFY rename patch.

The Phase-1 backfill set every legacy doc to transaction_type='Normal'.
This new patch flips that to 'VFY' across:
  - Custom Field options + default on 6 DocTypes
  - Existing rows in 6 tables
  - Client Script source bodies that still mention 'Normal'

Idempotency is critical — `bench migrate` may re-run this on subsequent
deploys; second run must be a no-op.
"""

import inspect
import frappe
from frappe.tests.utils import FrappeTestCase

from mhr.patches.v1_0 import rename_normal_to_vfy as patch


EXPECTED_DOCTYPES = {
    "Container",
    "Sales Order",
    "Delivery Note",
    "Stock Entry",
    "Print Batch",
    "Delivery Trip",
}


class TestRenamePatchPostState(FrappeTestCase):
    """The DB state after the patch ran in this session must show
    transaction_type options='VFY\\nHTY' on every DocType."""

    def test_custom_field_options_renamed(self):
        for dt in EXPECTED_DOCTYPES:
            with self.subTest(doctype=dt):
                cf = frappe.db.get_value(
                    "Custom Field",
                    {"dt": dt, "fieldname": "transaction_type"},
                    ["options", "default"],
                    as_dict=True,
                )
                self.assertIsNotNone(cf, f"transaction_type Custom Field missing on {dt}.")
                self.assertEqual(
                    set(cf.options.splitlines()),
                    {"VFY", "HTY"},
                    f"{dt}.transaction_type options must be exactly VFY+HTY (not Normal).",
                )
                self.assertEqual(
                    cf.default, "VFY",
                    f"{dt}.transaction_type default must be VFY (not Normal).",
                )


class TestRenamePatchListedInPatchesTxt(FrappeTestCase):

    def test_patch_registered_after_backfill(self):
        import os
        patches_path = os.path.join(frappe.get_app_path("mhr"), "patches.txt")
        content = open(patches_path).read()
        self.assertIn(
            "mhr.patches.v1_0.rename_normal_to_vfy",
            content,
            "rename_normal_to_vfy must be registered in patches.txt.",
        )
        # Must run AFTER the original backfill — the backfill sets rows to
        # 'Normal', and this patch then flips them to 'VFY'.
        backfill_idx = content.index("mhr.patches.v1_0.backfill_hty_transaction_type")
        rename_idx = content.index("mhr.patches.v1_0.rename_normal_to_vfy")
        self.assertGreater(
            rename_idx, backfill_idx,
            "Rename patch must follow the backfill patch — otherwise it has "
            "nothing to rename.",
        )


class TestRenamePatchBehavior(FrappeTestCase):
    """Source-level + idempotency tests."""

    def test_touches_all_six_doctypes(self):
        self.assertEqual(set(patch.DOCTYPES), EXPECTED_DOCTYPES,
            "DOCTYPES constant must list exactly the 6 DocTypes with transaction_type.")

    def test_touches_all_six_tables(self):
        expected_tables = {f"tab{d}" for d in EXPECTED_DOCTYPES}
        self.assertEqual(set(patch.TABLES), expected_tables)

    def test_update_targets_normal_only(self):
        """The UPDATE must filter WHERE transaction_type='Normal' so
        rows already at 'VFY' or 'HTY' are untouched. Without this guard
        the patch overwrites user-picked HTY values on rerun."""
        src = inspect.getsource(patch._migrate_existing_rows)
        self.assertIn(
            "WHERE transaction_type='Normal'", src,
            "_migrate_existing_rows must filter on transaction_type='Normal' — "
            "without this guard, a rerun could clobber HTY rows.",
        )

    def test_idempotent_rerun_is_noop(self):
        """Running the patch twice must leave the state identical."""
        patch.execute()  # first (might be no-op if session already ran it)
        patch.execute()  # second — must not raise, must leave 0 'Normal' rows
        for tbl in patch.TABLES:
            n = frappe.db.sql(
                f"SELECT COUNT(*) FROM `{tbl}` WHERE transaction_type='Normal'"
            )[0][0]
            self.assertEqual(
                n, 0,
                f"After patch, `{tbl}` must have 0 rows with transaction_type='Normal'.",
            )

    def test_client_scripts_no_longer_say_normal(self):
        """After the patch, no Client Script in module=Mhr should still
        contain the literal string 'Normal' in its body."""
        offenders = frappe.db.sql(
            """
            SELECT name FROM `tabClient Script`
            WHERE module = 'Mhr' AND script LIKE %s
            """,
            ("%Normal%",),
        )
        self.assertEqual(
            offenders, (),
            f"These Mhr Client Scripts still contain 'Normal': {offenders}. "
            "The rename patch must have flipped them to 'VFY'.",
        )
