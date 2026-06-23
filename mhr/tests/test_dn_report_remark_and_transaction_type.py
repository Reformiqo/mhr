"""MI1-I69 — DN Script Report SQL pins.

After converting DN from Query Report to Script Report, the SQL moved
from the Report doc's `query` field into mhr/mhr/report/dn/dn.py.
These tests pin the SQL's invariants on the new source-of-truth:
  - dn.remark surfaced as the Remark column (singular, not 'remarks')
  - VFY/HTY filtering via EXISTS on tabContainer.container_no (NOT
    tabContainer.name) — JOIN on name had 219k misses on real data.
"""

import inspect
import re

import frappe
from frappe.tests.utils import FrappeTestCase


def _get_data_src():
    from mhr.mhr.report.dn import dn as dn_module
    return inspect.getsource(dn_module.get_data)


class TestDnReportHasRemarkColumn(FrappeTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.src = _get_data_src()

    def test_remark_alias_present(self):
        self.assertIn("AS `remark`", self.src,
            "DN SELECT must include a remark column.")

    def test_remark_uses_dn_remark_singular(self):
        # The Delivery Note column is `remark` (singular), NOT `remarks`.
        self.assertIn("dn.remark", self.src)
        self.assertNotRegex(self.src, r"\bdn\.remarks\b",
            "Use dn.remark (singular). dn.remarks does not exist in v15.")

    def test_remark_aggregated_with_max(self):
        """MAX(dn.remark) satisfies ONLY_FULL_GROUP_BY without changing
        cardinality."""
        self.assertRegex(
            self.src,
            r"MAX\s*\(\s*dn\.remark\s*\)\s+AS\s+`remark`",
            "dn.remark must be aggregated with MAX().",
        )

    def test_remark_column_in_get_columns(self):
        from mhr.mhr.report.dn.dn import get_columns
        labels = [c["label"] for c in get_columns({})]
        self.assertIn("Remark", labels)


class TestDnReportTransactionTypeFilter(FrappeTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.src = _get_data_src()

    def test_filters_via_exists_subquery_on_container_no(self):
        """Pins the right-fix from the JOIN-on-name regression."""
        self.assertRegex(
            self.src,
            r"EXISTS\s*\(",
            "Transaction-type filter must use EXISTS (not JOIN).",
        )
        self.assertIn(
            "c.container_no = dni.custom_container_no",
            self.src,
            "EXISTS subquery must key on container_no (user-facing label).",
        )
        self.assertNotIn(
            "c.name = dni.custom_container_no",
            self.src,
            "Old broken JOIN-on-name pattern must NOT come back.",
        )

    def test_blank_filter_skips_exists_clause(self):
        """When transaction_type is '' or 'All', the EXISTS clause must
        be omitted entirely — gating-on-string-equality in Python is
        cleaner than an OR-clause in SQL."""
        self.assertRegex(
            self.src,
            r'transaction_type\s+in\s*\(\s*["\']VFY["\']\s*,\s*["\']HTY["\']\s*\)',
            "get_data must only append the EXISTS clause when "
            "transaction_type is 'VFY' or 'HTY'.",
        )


class TestDnRunsAcrossFilterValues(FrappeTestCase):
    """End-to-end: the Frappe runner must accept every value of
    transaction_type without exception."""

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

    def test_runs_with_all(self):
        self._run("All")

    def test_runs_with_vfy(self):
        self._run("VFY")

    def test_runs_with_hty(self):
        self._run("HTY")

    def test_remark_column_in_output(self):
        out = self._run("All")
        labels = [c.get("label") for c in (out.get("columns") or [])]
        self.assertIn("Remark", labels)

    def test_hty_swaps_labels(self):
        out = self._run("HTY")
        labels = [c.get("label") for c in (out.get("columns") or [])]
        self.assertIn("Type", labels,
            "HTY mode must rename Pulp -> Type in the columns dict the "
            "runner returns to the client.")
        self.assertIn("Product", labels,
            "HTY mode must rename Glue -> Product.")
        self.assertIn("Colour", labels,
            "HTY mode must rename Lusture -> Colour.")
