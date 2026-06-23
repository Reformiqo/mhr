"""MI1-I69 (DN report follow-up, 2026-06-23) — pin two additions:

1. The Remark column (sourced from dn.remark, aggregated with MAX so the
   GROUP BY stays sane) is in the SELECT.
2. The transaction_type filter actually filters rows — JOIN on tabContainer
   so dni.custom_container_no's Container.transaction_type is available in
   the WHERE; blank filter passes everything, VFY/HTY narrows.

A previous regression test (test_dn_report_filters_mi1_i69.py) already
pins that every %(placeholder)s in the SQL has a JS filter, so the
%(transaction_type)s placeholder is automatically covered there. These
tests add the specifics on top.
"""

import json
import os
import re

import frappe
from frappe.tests.utils import FrappeTestCase


def _load_dn_report_fixture():
    path = os.path.join(frappe.get_app_path("mhr"), "fixtures", "report.json")
    with open(path) as f:
        data = json.load(f)
    for r in data:
        if r.get("name") == "DN":
            return r
    raise RuntimeError("DN report not in fixtures/report.json")


class TestDnReportHasRemarkColumn(FrappeTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.query = (_load_dn_report_fixture().get("query") or "")

    def test_remark_alias_present(self):
        self.assertIn("AS `Remark`", self.query,
            "DN report SELECT must include a Remark column.")

    def test_remark_uses_dn_remark_singular(self):
        # The Delivery Note column is `remark` (singular), NOT `remarks`.
        # The earlier draft used `dn.remarks` and blew up with
        # 'Unknown column dn.remarks'.
        self.assertIn("dn.remark", self.query)
        self.assertNotRegex(self.query, r"\bdn\.remarks\b",
            "Use dn.remark (singular). dn.remarks does not exist in v15.")

    def test_remark_aggregated_with_max(self):
        """Within a GROUP BY (dn.name, item, container, lot), dn.remark
        is constant per group — but ONLY_FULL_GROUP_BY won't accept an
        un-aggregated reference. MAX() is correct because it preserves
        the value while satisfying the aggregator."""
        self.assertRegex(
            self.query,
            r"MAX\s*\(\s*dn\.remark\s*\)\s+AS\s+`Remark`",
            "dn.remark must be aggregated (MAX) to satisfy "
            "ONLY_FULL_GROUP_BY without changing cardinality.",
        )


class TestDnReportTransactionTypeFilter(FrappeTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.query = (_load_dn_report_fixture().get("query") or "")

    def test_joins_container_for_filtering(self):
        # We need Container.transaction_type to filter by, so JOIN on
        # tabContainer keyed on dni.custom_container_no.
        self.assertRegex(
            self.query,
            r"JOIN\s+`tabContainer`\s+\w+\s+ON\s+\w+\.name\s*=\s*dni\.custom_container_no",
            "DN report must LEFT JOIN tabContainer on dni.custom_container_no "
            "so transaction_type filtering works.",
        )

    def test_where_filters_by_transaction_type(self):
        # The WHERE clause must reference %(transaction_type)s and use
        # the joined Container.transaction_type.
        self.assertIn("%(transaction_type)s", self.query,
            "WHERE must consume the transaction_type filter from JS.")
        # Blank filter must pass everything (or-style guard).
        self.assertRegex(
            self.query,
            r"COALESCE\s*\(\s*NULLIF\s*\(\s*%\(transaction_type\)s\s*,\s*''\s*\)\s*,\s*''\s*\)\s*=\s*''",
            "Empty/blank transaction_type filter must allow all rows.",
        )


class TestDnRunsAcrossFilterValues(FrappeTestCase):
    """Smoke-test: run the report under each value of transaction_type
    and confirm Frappe accepts the call without exception. We don't
    assert row counts because the local DB may not have HTY rows."""

    def _run(self, transaction_type):
        from frappe.desk.query_report import run as run_report
        return run_report(
            report_name="DN",
            filters={
                "from_date": "2026-06-01",
                "to_date": "2026-06-30",
                "transaction_type": transaction_type,
            },
            ignore_prepared_report=True,
        )

    def test_runs_with_blank_transaction_type(self):
        self._run("")  # must not throw

    def test_runs_with_vfy(self):
        self._run("VFY")

    def test_runs_with_hty(self):
        self._run("HTY")

    def test_remark_column_in_output(self):
        out = self._run("")
        labels = [c.get("label") for c in (out.get("columns") or [])]
        self.assertIn("Remark", labels,
            "Report output must surface the Remark column to the client.")
