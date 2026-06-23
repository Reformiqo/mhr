"""MI1-I64 rework 2 — pin the on_change=report.refresh() wiring on the
transaction_type filter for Container Report and Stock Sheet (Balance Report).

Without on_change, changing the dropdown updates the URL but leaves the
header row stale until the user clicks Refresh manually — the column
labels (Pulp/Glue vs Type/Product) wouldn't swap. on_change forces an
immediate report.refresh() which re-runs execute() and re-fetches both
columns + data.
"""

import os

import frappe
from frappe.tests.utils import FrappeTestCase


class TestContainerReportFilterRefresh(FrappeTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        path = os.path.join(
            frappe.get_app_path("mhr"),
            "mhr", "report", "container_report", "container_report.js",
        )
        cls.js = open(path).read()

    def test_transaction_type_has_on_change(self):
        # The transaction_type filter block must contain on_change pointing
        # at frappe.query_report.refresh.
        self.assertIn('fieldname: "transaction_type"', self.js)
        self.assertRegex(
            self.js,
            r'on_change:\s*function\s*\(\s*\)\s*\{\s*frappe\.query_report\.refresh\(\)',
            "Container Report transaction_type filter must call "
            "frappe.query_report.refresh() on change so columns + data "
            "re-fetch immediately.",
        )


class TestBalanceReportFilterRefresh(FrappeTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        path = os.path.join(
            frappe.get_app_path("mhr"),
            "mhr", "report", "stock_sheet_(balance_report)",
            "stock_sheet_(balance_report).js",
        )
        cls.js = open(path).read()

    def test_transaction_type_has_on_change(self):
        self.assertIn('"fieldname": "transaction_type"', self.js)
        self.assertRegex(
            self.js,
            r'on_change:\s*function\s*\(\s*\)\s*\{\s*frappe\.query_report\.refresh\(\)',
            "Balance Report transaction_type filter must call "
            "frappe.query_report.refresh() on change.",
        )
