"""MI1-I50 P4 — Connections panel pin.

Pins the dashboard override that surfaces Receive entries linked back to
a Send-to-Subcontractor entry via custom_original_send_entry.
"""

import frappe
from frappe.tests.utils import FrappeTestCase


class TestStockEntryDashboardOverride(FrappeTestCase):

    def test_module_loadable(self):
        from mhr.overrides import stock_entry_dashboard
        self.assertTrue(callable(getattr(stock_entry_dashboard, "get_dashboard_data", None)),
            "mhr.overrides.stock_entry_dashboard.get_dashboard_data must exist.")

    def test_appends_subcontract_section(self):
        from mhr.overrides.stock_entry_dashboard import get_dashboard_data
        data = get_dashboard_data({"transactions": [{"label": "Reference", "items": ["Work Order"]}]})
        labels = [t["label"] for t in data["transactions"]]
        # _() of a literal is the literal in untranslated runtime contexts.
        self.assertIn("Subcontract", labels,
            "Override must add a 'Subcontract' section.")
        sect = next(t for t in data["transactions"] if t["label"] == "Subcontract")
        self.assertEqual(sect["items"], ["Stock Entry"],
            "Subcontract section must list Stock Entry as its linked doctype.")

    def test_preserves_existing_sections(self):
        """We APPEND — never clobber base ERPNext sections (Work Order, etc.)."""
        from mhr.overrides.stock_entry_dashboard import get_dashboard_data
        base = {"transactions": [
            {"label": "Reference", "items": ["Work Order", "Purchase Receipt"]},
        ]}
        out = get_dashboard_data(base)
        labels = [t["label"] for t in out["transactions"]]
        self.assertIn("Reference", labels,
            "Existing 'Reference' section must remain.")

    def test_idempotent_on_repeat_calls(self):
        """Restart can replay hooks on the cached dict; second call must not
        duplicate the Subcontract section."""
        from mhr.overrides.stock_entry_dashboard import get_dashboard_data
        data = get_dashboard_data({})
        data = get_dashboard_data(data)
        subs = [t for t in data["transactions"] if t["label"] == "Subcontract"]
        self.assertEqual(len(subs), 1,
            "Subcontract section must be added at most once.")

    def test_non_standard_fieldname_set(self):
        """Self-referential link needs an explicit fieldname map — the default
        Frappe resolution would look for a field called 'stock_entry'."""
        from mhr.overrides.stock_entry_dashboard import get_dashboard_data
        data = get_dashboard_data({})
        self.assertEqual(
            data["non_standard_fieldnames"]["Stock Entry"],
            "custom_original_send_entry",
            "Stock Entry → Stock Entry link must use custom_original_send_entry.",
        )

    def test_handles_empty_input(self):
        from mhr.overrides.stock_entry_dashboard import get_dashboard_data
        out = get_dashboard_data(None)
        self.assertIn("transactions", out)
        self.assertIn("non_standard_fieldnames", out)


class TestHooksWiring(FrappeTestCase):

    def test_override_doctype_dashboards_wired(self):
        from mhr import hooks
        self.assertIn("Stock Entry", hooks.override_doctype_dashboards,
            "Stock Entry must be in override_doctype_dashboards.")
        self.assertEqual(
            hooks.override_doctype_dashboards["Stock Entry"],
            "mhr.overrides.stock_entry_dashboard.get_dashboard_data",
            "Override must point at mhr.overrides.stock_entry_dashboard.get_dashboard_data.",
        )
