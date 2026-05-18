"""MI1-I39 Phase 2C — HTY transaction_type filter on existing reports.

Scope of this slice: Container Report + Delivery Challan only. The 4
stock_sheet reports (Balance, Balance Simple, Inward Cone Wise,
Inward Coneless, Inward Rest) will get the filter in a follow-up — they
have heavier data-loading internals and need careful edits to avoid
regressing the Normal-mode (legacy) Meher flow, which the FRD pins as
"must stay identical to today".

Tests pin:
  - Container Report adds a `transaction_type` JS filter + a Python
    post-aggregate filter that consults tabContainer.transaction_type.
  - Delivery Challan adds the JS filter + a SQL WHERE condition on
    dn.transaction_type.
  - Both treat NULL as 'Normal' (IFNULL pattern) so legacy docs
    created before the field existed don't disappear from Normal view.
  - Blank filter is a no-op (preserves backward compatibility).
"""

import inspect
import frappe
from frappe.tests.utils import FrappeTestCase

from mhr.mhr.report.container_report import container_report as cr
from mhr.mhr.report.delivery_challan import delivery_challan as dc


class TestContainerReportTransactionTypeFilter(FrappeTestCase):
    """Container Report — post-aggregate Python filter."""

    def test_blank_filter_is_noop(self):
        """Blank transaction_type must NOT filter — full result set returned."""
        cols_a, rows_a = cr.execute({})
        cols_b, rows_b = cr.execute({"transaction_type": ""})
        self.assertEqual(len(rows_a), len(rows_b),
            "Empty transaction_type filter must behave the same as no filter at all.")

    def test_filter_helper_treats_null_as_normal(self):
        """Source-level check: legacy Containers (NULL transaction_type)
        must appear under the Normal filter. The FRD's hard rule is that
        Normal = unchanged behavior."""
        src = inspect.getsource(cr._container_nos_for_transaction_type)
        self.assertIn(
            "IFNULL(transaction_type, 'Normal')",
            src,
            "Container Report's filter helper must IFNULL(transaction_type, 'Normal') — "
            "otherwise legacy Containers vanish from Normal-mode view.",
        )

    def test_filter_helper_only_submitted(self):
        src = inspect.getsource(cr._container_nos_for_transaction_type)
        self.assertIn(
            "docstatus = 1", src,
            "Filter helper must only consider submitted Containers (docstatus=1) — "
            "draft/cancelled don't represent active stock.",
        )

    def test_execute_invokes_filter_helper_only_when_set(self):
        src = inspect.getsource(cr.execute)
        self.assertIn(
            "_container_nos_for_transaction_type",
            src,
            "execute() must call the filter helper.",
        )
        # The filter must be guarded by `if tt:` so blank stays a no-op.
        self.assertIn("if tt:", src,
            "execute() must guard the filter behind `if tt:` so blank is a no-op.")


class TestDeliveryChallanTransactionTypeFilter(FrappeTestCase):
    """Delivery Challan — SQL WHERE condition on dn.transaction_type."""

    def test_blank_filter_does_not_modify_query(self):
        """No transaction_type → query runs without a transaction_type
        WHERE clause. We can't easily intercept the SQL, so we check
        the source flow gates the condition under `if transaction_type:`."""
        src = inspect.getsource(dc.get_data)
        self.assertIn("if transaction_type:", src,
            "Delivery Challan must guard the transaction_type WHERE under `if transaction_type:`.")

    def test_where_clause_present(self):
        src = inspect.getsource(dc.get_data)
        self.assertIn(
            "IFNULL(dn.transaction_type, 'Normal')",
            src,
            "Delivery Challan WHERE must IFNULL(dn.transaction_type, 'Normal') so legacy "
            "DNs appear under Normal — matches FRD's 'Normal = unchanged' rule.",
        )

    def test_filter_param_passed(self):
        src = inspect.getsource(dc.get_data)
        self.assertIn(
            'params["transaction_type"] = transaction_type',
            src,
            "Delivery Challan must add transaction_type to the params dict so the "
            "%(transaction_type)s placeholder resolves.",
        )

    def test_execute_runs_under_hty_filter(self):
        """Smoke: execute() returns a tuple even with the HTY filter set."""
        out = dc.execute({
            "from_date": "1900-01-01",
            "to_date": "1900-01-02",
            "transaction_type": "HTY",
        })
        self.assertEqual(len(out), 2)
        cols, rows = out
        self.assertIsInstance(cols, list)
        self.assertIsInstance(rows, list)
