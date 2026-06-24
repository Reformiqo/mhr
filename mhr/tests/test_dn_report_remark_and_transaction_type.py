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


class TestDnReportPerRowBatchAttributes(FrappeTestCase):
    """MI1-I64 follow-up (2026-06-24): per-row attributes (Pulp / Glue /
    Lusture / Grade / Denier / Item Length) must come from the linked
    Batch master, NOT from the DN header's aggregated copy.

    Previous SQL used COALESCE(NULLIF(dn.custom_*, ''), b.custom_*),
    which preferred the DN-level aggregated value (set by
    set_header_container_info_from_items). When a Sample Challan held
    several batches with different attributes, every row showed the
    same comma-joined / first-of-distinct header value."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.src = _get_data_src()

    def test_pulp_uses_max_batch_only(self):
        """Pulp must read from MAX(b.custom_pulp) — never from dn.custom_pulp."""
        self.assertRegex(
            self.src,
            r"SUBSTRING_INDEX\(MAX\(b\.custom_pulp\)",
            "pulp must come from the Batch master via MAX(b.custom_pulp).",
        )
        # Old buggy pattern must not return.
        self.assertNotIn("COALESCE(NULLIF(dn.custom_pulp", self.src,
            "Old COALESCE(dn.custom_pulp, b.custom_pulp) pattern caused "
            "every row to show the DN header's aggregated value.")

    def test_glue_lusture_grade_use_max_batch_only(self):
        for field in ("custom_glue", "custom_lusture", "custom_grade"):
            self.assertRegex(
                self.src,
                rf"SUBSTRING_INDEX\(MAX\(b\.{field}\)",
                f"{field} must come from the Batch master.",
            )
            self.assertNotIn(f"COALESCE(NULLIF(dn.{field}", self.src,
                f"Old COALESCE(dn.{field}, b.{field}) pattern caused "
                "every row to show the DN header's aggregated value.")

    def test_item_length_from_batch_not_count(self):
        """Item Length must read MAX(b.custom_total_item_length), not
        COUNT(dni.name) — COUNT collapsed all batches in the GROUP BY
        scope to a single number, ignoring the per-batch values."""
        self.assertIn("MAX(b.custom_total_item_length)", self.src,
            "item_length must come from the Batch master.")
        self.assertNotIn("COUNT(dni.name)", self.src,
            "Old COUNT(dni.name) pattern collapsed multi-batch rows.")

    def test_denier_from_batch_item(self):
        """Denier uses MAX(b.item) so it always matches the actual Batch
        master's item — falls back implicitly if Batch doesn't exist."""
        self.assertIn("MAX(b.item)", self.src,
            "denier must come from MAX(b.item) — Batch master canonical.")


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
