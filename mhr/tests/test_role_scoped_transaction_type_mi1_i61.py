"""MI1-I61 (Raj 2026-06-27) — restrict data visibility by user role.

Users assigned the 'HTY User' role must only see HTY records; 'VFY User'
role must only see VFY records. The Transaction Type filter on every
mhr report was letting single-mode users see the other mode's data by
tweaking the filter.

Fix: mhr.utilis.enforce_role_scoped_transaction_type() OVERWRITES
filters.transaction_type based on the calling user's roles, and every
mhr report calls it at the top of execute(). Admin / System Manager /
users with BOTH roles retain full access.
"""
import inspect
import os

import frappe
from frappe.tests.utils import FrappeTestCase


REPORTS = (
    "mhr.mhr.report.dn.dn",
    "mhr.mhr.report.container_report.container_report",
    "mhr.mhr.report.stock_sheet_(balance_report).stock_sheet_(balance_report)",
    "mhr.mhr.report.stock_sheet_(balance_report_simple).stock_sheet_(balance_report_simple)",
    "mhr.mhr.report.stock_sheet_(inward_cone_wise).stock_sheet_(inward_cone_wise)",
    "mhr.mhr.report.stock_sheets_(inward_coneless_stock_).stock_sheets_(inward_coneless_stock_)",
    "mhr.mhr.report.stock_sheets_(inward_rest_stock_).stock_sheets_(inward_rest_stock_)",
)


class TestEnforceHelperExists(FrappeTestCase):

    def test_helper_present(self):
        from mhr import utilis
        self.assertTrue(
            callable(getattr(utilis, "enforce_role_scoped_transaction_type", None)),
            "mhr.utilis.enforce_role_scoped_transaction_type must exist.",
        )

    def test_admin_bypasses(self):
        """Administrator must not have the transaction_type forced."""
        from mhr.utilis import enforce_role_scoped_transaction_type
        prev_user = frappe.session.user
        try:
            frappe.set_user("Administrator")
            f = enforce_role_scoped_transaction_type({"transaction_type": "HTY"})
            self.assertEqual(f.get("transaction_type"), "HTY",
                "Administrator must not be forced — HTY input stays HTY.")
            f = enforce_role_scoped_transaction_type({"transaction_type": "VFY"})
            self.assertEqual(f.get("transaction_type"), "VFY",
                "Administrator must not be forced — VFY input stays VFY.")
            f = enforce_role_scoped_transaction_type({})
            self.assertEqual(f.get("transaction_type"), None,
                "Administrator can see all — no forced default.")
        finally:
            frappe.set_user(prev_user)

    def test_helper_mutates_in_place_and_returns(self):
        """The function should mutate the input dict AND return it, so
        both call styles work."""
        from mhr.utilis import enforce_role_scoped_transaction_type
        prev_user = frappe.session.user
        try:
            frappe.set_user("Administrator")
            src = {"foo": "bar"}
            out = enforce_role_scoped_transaction_type(src)
            self.assertIs(out, src,
                "Helper must return the same dict object (mutating in place).")
        finally:
            frappe.set_user(prev_user)


class TestReportsCallEnforce(FrappeTestCase):
    """Structural pin — every mhr report's execute() body must call
    enforce_role_scoped_transaction_type. If someone adds a new mhr
    report without wiring the enforcement, the report becomes a bypass
    surface for the role restriction."""

    def test_each_report_calls_enforce(self):
        missing = []
        for mod_name in REPORTS:
            mod = frappe.get_module(mod_name)
            src = inspect.getsource(mod.execute)
            if "enforce_role_scoped_transaction_type" not in src:
                missing.append(mod_name)
        self.assertEqual(
            missing, [],
            f"These mhr reports skip the MI1-I61 role enforcement — data "
            f"leak surface: {missing}",
        )


class TestRolesCreatedByPatch(FrappeTestCase):

    def test_hty_user_role_exists(self):
        self.assertTrue(
            frappe.db.exists("Role", "HTY User"),
            "'HTY User' role must exist — created by the "
            "create_hty_vfy_roles patch.",
        )

    def test_vfy_user_role_exists(self):
        self.assertTrue(
            frappe.db.exists("Role", "VFY User"),
            "'VFY User' role must exist — created by the "
            "create_hty_vfy_roles patch.",
        )

    def test_patch_registered(self):
        path = os.path.join(frappe.get_app_path("mhr"), "patches.txt")
        body = open(path).read()
        self.assertIn(
            "mhr.patches.v1_0.create_hty_vfy_roles",
            body,
            "create_hty_vfy_roles patch must be in patches.txt.",
        )


class TestEnforceBehaviorWithRoles(FrappeTestCase):
    """Create a test user with only 'HTY User' role and verify the
    enforcement forces transaction_type='HTY' regardless of input."""

    def _make_user(self, email, roles):
        if frappe.db.exists("User", email):
            frappe.delete_doc("User", email, ignore_permissions=True, force=1)
        u = frappe.new_doc("User")
        u.email = email
        u.first_name = email.split("@")[0]
        u.enabled = 1
        u.new_password = "Test@1234"
        u.send_welcome_email = 0
        for r in roles:
            u.append("roles", {"role": r})
        u.insert(ignore_permissions=True)
        return u.name

    def test_hty_only_user_gets_hty_forced(self):
        from mhr.utilis import enforce_role_scoped_transaction_type
        prev = frappe.session.user
        try:
            uname = self._make_user("mi1i61-hty@example.com", ["HTY User"])
            frappe.set_user(uname)
            # User tries to see VFY data — enforcement flips to HTY.
            f = enforce_role_scoped_transaction_type({"transaction_type": "VFY"})
            self.assertEqual(
                f.get("transaction_type"), "HTY",
                "HTY-only user's 'VFY' filter must be overridden to 'HTY'.",
            )
            # No filter at all → default to HTY.
            f = enforce_role_scoped_transaction_type({})
            self.assertEqual(
                f.get("transaction_type"), "HTY",
                "HTY-only user without a filter must default to HTY.",
            )
        finally:
            frappe.set_user(prev)

    def test_vfy_only_user_gets_vfy_forced(self):
        from mhr.utilis import enforce_role_scoped_transaction_type
        prev = frappe.session.user
        try:
            uname = self._make_user("mi1i61-vfy@example.com", ["VFY User"])
            frappe.set_user(uname)
            f = enforce_role_scoped_transaction_type({"transaction_type": "HTY"})
            self.assertEqual(
                f.get("transaction_type"), "VFY",
                "VFY-only user's 'HTY' filter must be overridden to 'VFY'.",
            )
        finally:
            frappe.set_user(prev)

    def test_both_roles_user_keeps_choice(self):
        """A user with BOTH roles is treated as full access — no override."""
        from mhr.utilis import enforce_role_scoped_transaction_type
        prev = frappe.session.user
        try:
            uname = self._make_user(
                "mi1i61-both@example.com", ["HTY User", "VFY User"],
            )
            frappe.set_user(uname)
            f = enforce_role_scoped_transaction_type({"transaction_type": "HTY"})
            self.assertEqual(f.get("transaction_type"), "HTY",
                "Dual-role user's HTY choice must NOT be overridden.")
            f = enforce_role_scoped_transaction_type({"transaction_type": "VFY"})
            self.assertEqual(f.get("transaction_type"), "VFY",
                "Dual-role user's VFY choice must NOT be overridden.")
        finally:
            frappe.set_user(prev)
