"""MI1-I61 (Raj 2026-06-27) + MI1-I80 (Raj 2026-07-15) — restrict
report data visibility based on the caller's User Permission for
Transaction Type.

Semantics per Raj's spec:
  * Case 1 — User Permission [Allow=Transaction Type, For=HTY] only
    → mhr reports force transaction_type='HTY'
  * Case 2 — User Permission [Allow=Transaction Type, For=VFY] only
    → mhr reports force transaction_type='VFY'
  * Case 3 — Both HTY and VFY permissions → no forcing
  * Case 4 — No Transaction Type permission → no forcing (default)

Bypass regardless:
  * Administrator
  * Any user with 'System Manager'
"""
import inspect

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
        """Administrator must never have the transaction_type forced."""
        from mhr.utilis import enforce_role_scoped_transaction_type
        prev = frappe.session.user
        try:
            frappe.set_user("Administrator")
            f = enforce_role_scoped_transaction_type({"transaction_type": "HTY"})
            self.assertEqual(f.get("transaction_type"), "HTY")
            f = enforce_role_scoped_transaction_type({"transaction_type": "VFY"})
            self.assertEqual(f.get("transaction_type"), "VFY")
            f = enforce_role_scoped_transaction_type({})
            self.assertEqual(f.get("transaction_type"), None,
                "Administrator sees no forced default when no filter.")
        finally:
            frappe.set_user(prev)

    def test_helper_reads_user_permission_not_role(self):
        """MI1-I80: switched from role-based to User Permission-based."""
        from mhr import utilis
        src = inspect.getsource(utilis.enforce_role_scoped_transaction_type)
        self.assertIn(
            "tabUser Permission",
            src,
            "Helper must query the tabUser Permission table (not roles) "
            "per Raj's 2026-07-15 spec.",
        )
        self.assertIn(
            "allow = 'Transaction Type'",
            src,
            "Helper must scope the query to allow='Transaction Type'.",
        )


class TestReportsCallEnforce(FrappeTestCase):
    """Every mhr report must call the enforce helper at execute() top —
    otherwise the report becomes a bypass surface."""

    def test_each_report_calls_enforce(self):
        missing = []
        for mod_name in REPORTS:
            mod = frappe.get_module(mod_name)
            src = inspect.getsource(mod.execute)
            if "enforce_role_scoped_transaction_type" not in src:
                missing.append(mod_name)
        self.assertEqual(
            missing, [],
            f"These mhr reports skip the enforcement: {missing}",
        )


class TestUserPermissionBehavior(FrappeTestCase):
    """End-to-end: create test users with each permission config and
    verify enforce_role_scoped_transaction_type honours Raj's four cases."""

    USER_HTY = "mi1i80-hty@example.com"
    USER_VFY = "mi1i80-vfy@example.com"
    USER_BOTH = "mi1i80-both@example.com"
    USER_NONE = "mi1i80-none@example.com"

    @classmethod
    def _make_user(cls, email):
        if frappe.db.exists("User", email):
            frappe.delete_doc("User", email, ignore_permissions=True, force=1)
        u = frappe.new_doc("User")
        u.email = email
        u.first_name = email.split("@")[0]
        u.enabled = 1
        u.new_password = "Test@1234"
        u.send_welcome_email = 0
        u.insert(ignore_permissions=True)
        return u.name

    @classmethod
    def _grant(cls, user, for_value):
        # Skip if a Transaction Type doc with for_value doesn't exist.
        if not frappe.db.exists("Transaction Type", for_value):
            return
        up = frappe.new_doc("User Permission")
        up.user = user
        up.allow = "Transaction Type"
        up.for_value = for_value
        up.apply_to_all_doctypes = 1
        up.insert(ignore_permissions=True)

    def setUp(self):
        for u in (self.USER_HTY, self.USER_VFY, self.USER_BOTH, self.USER_NONE):
            self._make_user(u)
        self._grant(self.USER_HTY, "HTY")
        self._grant(self.USER_VFY, "VFY")
        self._grant(self.USER_BOTH, "HTY")
        self._grant(self.USER_BOTH, "VFY")
        frappe.db.commit()

    def tearDown(self):
        for u in (self.USER_HTY, self.USER_VFY, self.USER_BOTH, self.USER_NONE):
            # Delete User Permissions first.
            for up in frappe.db.get_all("User Permission", filters={"user": u}, fields=["name"]):
                frappe.delete_doc("User Permission", up["name"], ignore_permissions=True, force=1)
            if frappe.db.exists("User", u):
                frappe.delete_doc("User", u, ignore_permissions=True, force=1)
        frappe.db.commit()

    def test_case_1_hty_only_forces_hty(self):
        from mhr.utilis import enforce_role_scoped_transaction_type
        prev = frappe.session.user
        try:
            frappe.set_user(self.USER_HTY)
            # User attempts to view VFY — must be forced to HTY.
            f = enforce_role_scoped_transaction_type({"transaction_type": "VFY"})
            self.assertEqual(
                f.get("transaction_type"), "HTY",
                "Case 1 — HTY-only user's VFY filter must be overridden.",
            )
            # No filter → default forced to HTY.
            f = enforce_role_scoped_transaction_type({})
            self.assertEqual(f.get("transaction_type"), "HTY",
                "Case 1 — HTY-only user with no filter must default to HTY.")
        finally:
            frappe.set_user(prev)

    def test_case_2_vfy_only_forces_vfy(self):
        from mhr.utilis import enforce_role_scoped_transaction_type
        prev = frappe.session.user
        try:
            frappe.set_user(self.USER_VFY)
            f = enforce_role_scoped_transaction_type({"transaction_type": "HTY"})
            self.assertEqual(f.get("transaction_type"), "VFY",
                "Case 2 — VFY-only user's HTY filter must be overridden.")
        finally:
            frappe.set_user(prev)

    def test_case_3_both_permissions_no_forcing(self):
        from mhr.utilis import enforce_role_scoped_transaction_type
        prev = frappe.session.user
        try:
            frappe.set_user(self.USER_BOTH)
            f = enforce_role_scoped_transaction_type({"transaction_type": "HTY"})
            self.assertEqual(f.get("transaction_type"), "HTY",
                "Case 3 — dual-permission user's HTY pick must survive.")
            f = enforce_role_scoped_transaction_type({"transaction_type": "VFY"})
            self.assertEqual(f.get("transaction_type"), "VFY",
                "Case 3 — dual-permission user's VFY pick must survive.")
        finally:
            frappe.set_user(prev)

    def test_case_4_no_permission_no_forcing(self):
        from mhr.utilis import enforce_role_scoped_transaction_type
        prev = frappe.session.user
        try:
            frappe.set_user(self.USER_NONE)
            # No permission → filters pass through unchanged.
            f = enforce_role_scoped_transaction_type({"transaction_type": "HTY"})
            self.assertEqual(f.get("transaction_type"), "HTY",
                "Case 4 — no-permission user's HTY pick must survive.")
            f = enforce_role_scoped_transaction_type({})
            self.assertEqual(f.get("transaction_type"), None,
                "Case 4 — no-permission user with no filter must see all.")
        finally:
            frappe.set_user(prev)
