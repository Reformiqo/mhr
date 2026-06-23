"""MI1-I69 (the Zoho ticket, not my earlier commit-tag mix-up, 2026-06-23):
Purchase Receipt posting_date must match the Container Inward date.

Raj's bug: every Container Inward generates a Purchase Receipt with a
stale 2025-12-28 date — the Property Setter
'Container-posting_date-default' was pinning the field to that literal
value. New Containers inherited it; PRs inherited it from the
Container. Now fixed two ways:

  1. Property Setter changed: default 2025-12-28 -> Today.
  2. Both create_purchase_receipt code paths (container.py + job.py)
     fall back to today() when container.posting_date is falsy — covers
     legacy rows without a date.
"""

import inspect

import frappe
from frappe.tests.utils import FrappeTestCase


class TestContainerDefaultIsToday(FrappeTestCase):
    """Pin the Property Setter so the literal 2025-12-28 default never
    sneaks back in."""

    def test_property_setter_value_is_today(self):
        value = frappe.db.get_value(
            "Property Setter", "Container-posting_date-default", "value")
        self.assertEqual(value, "Today",
            "Container.posting_date default must be 'Today' (Frappe's "
            "standard today-keyword), NOT a literal date.")

    def test_property_setter_no_literal_date(self):
        value = frappe.db.get_value(
            "Property Setter", "Container-posting_date-default", "value") or ""
        # Defensive: catch any future-rollback to a literal 4-digit year.
        import re
        self.assertNotRegex(value, r"^\d{4}-\d{2}-\d{2}$",
            "Container.posting_date default must not be a literal date "
            "string (was 2025-12-28; that stale value triggered MI1-I69).")


class TestCreatePrFallback(FrappeTestCase):
    """Source-level pin: both create_purchase_receipt code paths must
    fall back to today() when container.posting_date is missing/falsy."""

    def test_container_method_uses_fallback(self):
        from mhr.mhr.doctype.container.container import Container
        src = inspect.getsource(Container.create_purchase_receipt)
        self.assertIn(
            "self.posting_date or frappe.utils.today()", src,
            "Container.create_purchase_receipt must fall back to today() "
            "when self.posting_date is falsy.",
        )

    def test_container_method_sets_set_posting_time(self):
        """ERPNext's StockController resets posting_date to today() on
        validate unless set_posting_time=1. Without this flag, a
        back-dated Container Inward (e.g. 2025-12-01) silently produces
        a PR dated today — exactly Raj's 2026-06-23 follow-up screenshot."""
        from mhr.mhr.doctype.container.container import Container
        src = inspect.getsource(Container.create_purchase_receipt)
        self.assertIn(
            "purchase_receipt.set_posting_time = 1", src,
            "set_posting_time = 1 MUST be set on the PR — otherwise "
            "ERPNext clobbers our posting_date back to today.",
        )

    def test_job_module_uses_fallback(self):
        from mhr import job as job_mod
        src = inspect.getsource(job_mod.create_purchase_receipt)
        self.assertIn(
            "container.posting_date or frappe.utils.today()", src,
            "mhr.job.create_purchase_receipt must use the same fallback "
            "as the Container method — same behaviour on either path.",
        )

    def test_job_module_sets_set_posting_time(self):
        from mhr import job as job_mod
        src = inspect.getsource(job_mod.create_purchase_receipt)
        self.assertIn(
            "purchase_receipt.set_posting_time = 1", src,
            "mhr.job.create_purchase_receipt must ALSO set "
            "set_posting_time = 1 — same StockController reset bug "
            "applies to the queued path.",
        )


class TestNoLiteralDateInSource(FrappeTestCase):
    """Regression guard: nobody should re-introduce the 2025-12-28
    literal in the Container creation paths."""

    def test_container_module_no_2025_12_28(self):
        from mhr.mhr.doctype.container import container as c_mod
        src = inspect.getsource(c_mod)
        # Allow it in COMMENTS / docstrings (we have context in this
        # commit). Catch it in real code.
        import re
        no_comments = re.sub(r"#[^\n]*", "", src)
        no_docs = re.sub(r'""".*?"""', "", no_comments, flags=re.DOTALL)
        self.assertNotIn("2025-12-28", no_docs,
            "The stale 2025-12-28 default must not appear in live code.")
