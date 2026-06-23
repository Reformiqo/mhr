"""MI1-I50 P5 — Subcontractor Material Tracking report pins.

Behavioural integration with real Stock Entries is deferred to P6 — these
tests pin the report's contract:
  - module exists and execute(filters) returns (columns, data)
  - column shape matches what the JS formatter expects (fieldnames pending_qty,
    status, etc.)
  - SQL respects the date/supplier/status filters
  - report doc JSON has prepared_report=1 + ref_doctype=Stock Entry
"""

import frappe
from frappe.tests.utils import FrappeTestCase


REPORT_MODULE = "mhr.mhr.report.subcontractor_material_tracking.subcontractor_material_tracking"


class TestReportShape(FrappeTestCase):

    def test_execute_signature(self):
        mod = frappe.get_module(REPORT_MODULE)
        self.assertTrue(callable(getattr(mod, "execute", None)),
            "Report module must expose execute(filters).")

    def test_execute_returns_columns_and_data(self):
        mod = frappe.get_module(REPORT_MODULE)
        # Date range outside any real data so we get an empty list back
        # without depending on test fixtures (P5 is filter-shape, not data).
        cols, data = mod.execute({"from_date": "1900-01-01", "to_date": "1900-01-01"})
        self.assertIsInstance(cols, list)
        self.assertIsInstance(data, list)
        self.assertGreater(len(cols), 0, "Report must declare at least one column.")

    def test_column_fieldnames_match_js_formatter(self):
        """The JS formatter references pending_qty + status by fieldname —
        renames here would silently break the colouring."""
        from mhr.mhr.report.subcontractor_material_tracking.subcontractor_material_tracking import get_columns
        fieldnames = {c["fieldname"] for c in get_columns()}
        for required in (
            "send_entry", "posting_date", "supplier", "item_code",
            "batch_no", "sent_qty", "received_qty", "pending_qty", "status",
        ):
            self.assertIn(required, fieldnames,
                f"Column {required} must be in the report (JS / users rely on it).")

    def test_status_filter_open_includes_nulls(self):
        """Legacy Send entries pre-dating P3 have NULL status — the 'Open'
        filter must still surface them."""
        import inspect
        from mhr.mhr.report.subcontractor_material_tracking import subcontractor_material_tracking as mod
        src = inspect.getsource(mod.get_data)
        self.assertIn("IS NULL", src,
            "Open-status filter must treat NULL custom_subcontract_status as Open.")
        self.assertIn("Send to Subcontractor", src,
            "Report must restrict to Send-to-Subcontractor entries.")
        self.assertIn("docstatus = 1", src,
            "Report must only include Submitted entries.")

    def test_pending_qty_recomputed_defensively(self):
        """Pending column falls back to qty - received when custom_pending_qty
        is NULL (entries that pre-date the P3 recompute hook)."""
        import inspect
        from mhr.mhr.report.subcontractor_material_tracking import subcontractor_material_tracking as mod
        src = inspect.getsource(mod.get_data)
        self.assertIn("COALESCE(sed.custom_pending_qty", src,
            "Pending must COALESCE custom_pending_qty with qty - received.")


class TestReportDocJson(FrappeTestCase):
    """Pin the report doc JSON — fixtures are how this reaches production."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        import json
        import os
        path = os.path.join(
            frappe.get_app_path("mhr"),
            "mhr", "report", "subcontractor_material_tracking",
            "subcontractor_material_tracking.json",
        )
        with open(path) as f:
            cls.doc = json.load(f)

    def test_doctype_and_module(self):
        self.assertEqual(self.doc["doctype"], "Report")
        self.assertEqual(self.doc["module"], "Mhr")
        self.assertEqual(self.doc["report_type"], "Script Report")

    def test_ref_doctype(self):
        self.assertEqual(self.doc["ref_doctype"], "Stock Entry",
            "ref_doctype controls where the report is listed; must be Stock Entry.")

    def test_prepared_report_enabled(self):
        self.assertEqual(self.doc["prepared_report"], 1,
            "prepared_report=1 lets Frappe cache results in Redis — required "
            "given 100K+ batches in prod.")

    def test_roles_include_stock_user(self):
        roles = [r["role"] for r in self.doc.get("roles", [])]
        self.assertIn("Stock User", roles)
        self.assertIn("Stock Manager", roles)


class TestJsFormatter(FrappeTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        import os
        path = os.path.join(
            frappe.get_app_path("mhr"),
            "mhr", "report", "subcontractor_material_tracking",
            "subcontractor_material_tracking.js",
        )
        cls.js = open(path).read()

    def test_formatter_targets_pending_and_status(self):
        self.assertIn('column.fieldname === "pending_qty"', self.js,
            "Formatter must colour pending_qty when > 0.")
        self.assertIn('column.fieldname === "status"', self.js,
            "Formatter must colour the status column.")
        self.assertIn("Fully Received", self.js)
        self.assertIn("Partially Received", self.js)

    def test_filters_include_supplier_and_status(self):
        for f in ("from_date", "to_date", "supplier", "status"):
            self.assertIn(f'"{f}"', self.js,
                f"Report JS must declare the {f} filter.")
