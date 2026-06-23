"""MI1-I70 (2026-06-23) — Transaction Type Select → Link to DocType.

Pins:
  - The Transaction Type DocType exists with the right autoname.
  - Seed rows VFY and HTY are present after migrate.
  - All 6 transaction_type Custom Fields are now Link fields pointing
    at 'Transaction Type' (no longer Select with hardcoded options).
  - The conversion patch is registered in patches.txt.
"""

import os

import frappe
from frappe.tests.utils import FrappeTestCase


CUSTOM_FIELDS = (
    "Container-transaction_type",
    "Delivery Note-transaction_type",
    "Delivery Trip-transaction_type",
    "Print Batch-transaction_type",
    "Sales Order-transaction_type",
    "Stock Entry-transaction_type",
)


class TestTransactionTypeDoctype(FrappeTestCase):

    def test_doctype_exists(self):
        self.assertTrue(frappe.db.exists("DocType", "Transaction Type"))

    def test_autoname_is_field(self):
        meta = frappe.get_meta("Transaction Type")
        self.assertEqual(meta.autoname, "field:transaction_type_name",
            "autoname must be field:transaction_type_name so legacy "
            "string values map straight to document names.")

    def test_seed_values_present(self):
        for name in ("VFY", "HTY"):
            self.assertTrue(
                frappe.db.exists("Transaction Type", name),
                f"Seed value {name!r} must exist after migrate "
                "(installed by mhr.patches.v1_0.convert_transaction_type_to_link).",
            )


class TestCustomFieldsAreLink(FrappeTestCase):
    """All six transaction_type Custom Fields must be Link(Transaction Type)."""

    def test_all_six_are_link(self):
        for cf in CUSTOM_FIELDS:
            fieldtype, options = frappe.db.get_value(
                "Custom Field", cf, ["fieldtype", "options"]
            ) or (None, None)
            self.assertEqual(fieldtype, "Link",
                f"{cf} must be Link (was Select with VFY|HTY options).")
            self.assertEqual(options, "Transaction Type",
                f"{cf}.options must be 'Transaction Type'.")

    def test_no_select_with_old_options(self):
        """Regression guard: nobody re-introduces the hardcoded VFY|HTY
        Select options on any of the six fields."""
        for cf in CUSTOM_FIELDS:
            options = frappe.db.get_value("Custom Field", cf, "options")
            self.assertNotIn(
                "\n", options or "",
                f"{cf}.options must not be a multi-line Select list — "
                "values are now Transaction Type docs.",
            )


class TestPatchRegistered(FrappeTestCase):

    def test_patch_in_patches_txt(self):
        path = os.path.join(frappe.get_app_path("mhr"), "patches.txt")
        body = open(path).read()
        self.assertIn(
            "mhr.patches.v1_0.convert_transaction_type_to_link",
            body,
            "Migration patch must be registered in patches.txt so it "
            "runs on every site's next bench migrate.",
        )

    def test_patch_module_loadable(self):
        from mhr.patches.v1_0 import convert_transaction_type_to_link as p
        self.assertTrue(callable(getattr(p, "execute", None)),
            "Patch module must expose execute().")
        # Source-level pin on what the patch actually does.
        import inspect
        src = inspect.getsource(p.execute)
        self.assertIn("Transaction Type", src)
        # Flips fieldtype to Link
        self.assertIn('"fieldtype": "Link"', src)
        # Idempotency via frappe.db.exists check before insert
        self.assertIn('frappe.db.exists("Transaction Type"', src,
            "Patch must check existence before inserting the seed rows.")


class TestReportsStillWorkWithLinkFilter(FrappeTestCase):
    """End-to-end: the DN + Container reports' transaction_type filter
    must keep working after the Select->Link flip. The filter value is
    still a string ('VFY' / 'HTY') because that's the Transaction Type
    document name — nothing in the query layer changes."""

    def test_dn_report_runs_with_vfy(self):
        from frappe.desk.query_report import run as run_report
        # Must not throw — value is 'VFY' (a valid Transaction Type doc name)
        run_report(
            report_name="DN",
            filters={
                "from_date": "2026-06-01",
                "to_date": "2026-06-30",
                "transaction_type": "VFY",
            },
            ignore_prepared_report=True,
        )

    def test_container_report_runs_with_hty(self):
        from frappe.desk.query_report import run as run_report
        run_report(
            report_name="Container Report",
            filters={"transaction_type": "HTY"},
            ignore_prepared_report=True,
        )
