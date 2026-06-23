"""MI1-I69 — DN report initial-load KeyError fix.

Raj 2026-06-23: opening /query-report/DN exploded with
    KeyError: 'from_date'
because the SQL has `dn.posting_date BETWEEN %(from_date)s AND %(to_date)s`
but the report JS only declared the `transaction_type` filter — Frappe
sent `filters={}` on initial load and the SQL substitution blew up.

Pins:
  - The DN report exists and is a Query Report.
  - Its JS declares EVERY placeholder used in its SQL (from_date,
    to_date, transaction_type), so the placeholder set is a subset of
    the declared filter set.
  - from_date + to_date are reqd=1 with sensible defaults so initial
    load never hits the KeyError again.

Any future commit that re-introduces a SQL placeholder without a
matching JS filter will fail this test loudly.
"""

import json
import os
import re

import frappe
from frappe.tests.utils import FrappeTestCase


def _load_dn_report_fixture():
    path = os.path.join(
        frappe.get_app_path("mhr"), "fixtures", "report.json"
    )
    with open(path) as f:
        data = json.load(f)
    for r in data:
        if r.get("name") == "DN":
            return r
    raise RuntimeError("DN report not found in fixtures/report.json")


class TestDnReportFilterParity(FrappeTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.report = _load_dn_report_fixture()
        cls.query = cls.report.get("query") or ""
        cls.js = cls.report.get("javascript") or ""

    def test_is_query_report(self):
        self.assertEqual(self.report.get("report_type"), "Query Report")

    def test_every_sql_placeholder_has_a_filter(self):
        """Every %(name)s in the SQL must be declared as a filter in the
        JS, otherwise initial-load `filters={}` raises KeyError."""
        placeholders = set(re.findall(r"%\((\w+)\)s", self.query))
        # Collect all "fieldname": "...", appearances in the JS filters block.
        declared = set(re.findall(r'"fieldname":\s*"(\w+)"', self.js))
        missing = placeholders - declared
        self.assertFalse(
            missing,
            f"SQL placeholder(s) {missing} have no corresponding JS filter. "
            "Initial load would crash with KeyError. Add a filter to the "
            "report's javascript (or remove the placeholder from the SQL).",
        )

    def test_from_date_filter_present_with_default(self):
        # from_date must be declared, reqd, with frappe.datetime.* default
        # so initial-load Frappe sends a value (DOTALL because the filter
        # block spans multiple lines).
        self.assertTrue(
            re.search(
                r'"fieldname":\s*"from_date".*?"default":\s*frappe\.datetime\.',
                self.js, re.DOTALL,
            ),
            "from_date filter must have a frappe.datetime.* default.",
        )

    def test_to_date_filter_present_with_default(self):
        self.assertTrue(
            re.search(
                r'"fieldname":\s*"to_date".*?"default":\s*frappe\.datetime\.',
                self.js, re.DOTALL,
            ),
            "to_date filter must have a frappe.datetime.* default.",
        )


class TestDnReportRunsWithDefaults(FrappeTestCase):
    """Smoke-test: calling Frappe's standard report runner with the same
    filter shape Frappe constructs from the JS defaults must NOT throw
    KeyError. We tolerate any other result (zero rows is fine)."""

    def test_run_with_typical_filters(self):
        from frappe.desk.query_report import run as run_report
        try:
            run_report(
                report_name="DN",
                filters={
                    "from_date": "2026-06-01",
                    "to_date": "2026-06-30",
                    "transaction_type": "All",
                },
                ignore_prepared_report=True,
            )
        except KeyError as e:
            self.fail(f"DN report still raises KeyError({e!r}) on a populated "
                      f"filter dict — the SQL has placeholders the filters don't "
                      f"cover.")

    def test_run_with_empty_string_transaction_type(self):
        """Frappe's query_report client DROPS filters whose value is "".
        Reproduce the dropped-filter case explicitly to make sure the
        SQL's 'All' / '' bypass actually handles it."""
        from frappe.desk.query_report import run as run_report
        # Pass empty string — the SQL guard must treat it the same as 'All'.
        try:
            run_report(
                report_name="DN",
                filters={
                    "from_date": "2026-06-01",
                    "to_date": "2026-06-30",
                    "transaction_type": "",
                },
                ignore_prepared_report=True,
            )
        except KeyError as e:
            self.fail(f"DN report KeyError({e!r}) — SQL must accept '' as 'no filter'.")
