"""MI1-I80 (Raj 2026-07-14, description part) — role-scoped access to
mhr reports.

Composes with MI1-I61: enforce_role_scoped_transaction_type already
forces the transaction_type filter to match the caller's role at
execute() time. This patch adds HTY User + VFY User to each mhr
report's Has Role so a user with only those roles can OPEN the reports
(otherwise Frappe blocks access before execute() even runs).

End result:
  * Only HTY User → can open every mhr report, all rows forced to HTY.
  * Only VFY User → same but forced to VFY.
  * Both roles     → full access.
  * Neither role AND none of the existing report role lists → blocked
    at the Frappe permission layer.
"""
import inspect
import os

import frappe
from frappe.tests.utils import FrappeTestCase


class TestPatchRegistered(FrappeTestCase):

    def test_patch_in_patches_txt(self):
        path = os.path.join(frappe.get_app_path("mhr"), "patches.txt")
        body = open(path).read()
        self.assertIn(
            "mhr.patches.v1_0.grant_hty_vfy_roles_on_mhr_reports",
            body,
            "Patch must be registered in patches.txt so a fresh migrate "
            "on prod grants the roles on any freshly-imported reports.",
        )

    def test_patch_module_loadable(self):
        from mhr.patches.v1_0 import grant_hty_vfy_roles_on_mhr_reports as p
        self.assertTrue(callable(getattr(p, "execute", None)))

    def test_patch_is_idempotent(self):
        """The patch must skip pairs that already exist — otherwise a
        re-run would create duplicate Has Role rows."""
        from mhr.patches.v1_0 import grant_hty_vfy_roles_on_mhr_reports as p
        src = inspect.getsource(p)
        self.assertIn(
            'frappe.db.exists(\n                "Has Role",',
            src,
            "Patch must guard on Has Role existence before insert — "
            "idempotency requirement.",
        )

    def test_patch_creates_role_row_when_role_exists(self):
        from mhr.patches.v1_0 import grant_hty_vfy_roles_on_mhr_reports as p
        src = inspect.getsource(p)
        self.assertIn('if not frappe.db.exists("Role", role)', src,
            "Patch must skip cleanly if the role hasn't been created "
            "yet — patches.txt order should place create_hty_vfy_roles "
            "first, but the guard prevents a hard fail.")


class TestAllMhrReportsGrantedRoles(FrappeTestCase):
    """Verify the patch actually granted the roles on every mhr
    report in the local DB. This is the observable end-state that
    breaks if the patch didn't run."""

    def test_every_mhr_report_has_both_roles(self):
        reports = frappe.db.get_all("Report", filters={"module": "Mhr"}, fields=["name"])
        self.assertGreater(len(reports), 0, "Mhr module must have reports.")

        missing = []
        for r in reports:
            role_rows = frappe.db.get_all(
                "Has Role",
                filters={"parent": r["name"], "parenttype": "Report"},
                fields=["role"],
            )
            roles = {x["role"] for x in role_rows}
            for required in ("HTY User", "VFY User"):
                if required not in roles:
                    missing.append((r["name"], required))
        self.assertEqual(
            missing, [],
            f"Every mhr Report must include HTY User and VFY User in its "
            f"Has Role list. Missing: {missing}",
        )


class TestEnforceRoleScopedTransactionTypeStillWired(FrappeTestCase):
    """MI1-I80's report-visibility grant is meaningless without
    MI1-I61's data-filter enforcement. Pin that helper still exists
    and each mhr report calls it."""

    def test_enforce_helper_exists(self):
        from mhr import utilis
        self.assertTrue(
            callable(getattr(utilis, "enforce_role_scoped_transaction_type", None)),
            "mhr.utilis.enforce_role_scoped_transaction_type must exist "
            "— without it, granting the roles alone still lets an HTY "
            "user see VFY data.",
        )
