"""MI1-I39 Phase 2C — HTY transaction_type filter on existing reports.

Scope of this slice: Container Report + Delivery Challan only. The 4
stock_sheet reports (Balance, Balance Simple, Inward Cone Wise,
Inward Coneless, Inward Rest) will get the filter in a follow-up — they
have heavier data-loading internals and need careful edits to avoid
regressing the VFY-mode (legacy) Meher flow, which the FRD pins as
"must stay identical to today".

Tests pin:
  - Container Report adds a `transaction_type` JS filter + a Python
    post-aggregate filter that consults tabContainer.transaction_type.
  - Delivery Challan adds the JS filter + a SQL WHERE condition on
    dn.transaction_type.
  - Both treat NULL as 'VFY' (IFNULL pattern) so legacy docs
    created before the field existed don't disappear from VFY view.
  - Blank filter is a no-op (preserves backward compatibility).
"""

import inspect
import frappe
from frappe.tests.utils import FrappeTestCase

from mhr.mhr.report.container_report import container_report as cr
from mhr.mhr.report.delivery_challan import delivery_challan as dc
from mhr import utilis as mhr_utilis


class TestSharedHTYFilterHelper(FrappeTestCase):
    """The shared helpers in mhr/utilis.py are reused by Container Report
    and the 4 stock_sheet reports. A regression here would cascade across
    every report."""

    def test_helper_returns_none_for_blank(self):
        # Blank input → None ("no filter") so callers can short-circuit.
        self.assertIsNone(mhr_utilis.get_container_nos_by_transaction_type(""))
        self.assertIsNone(mhr_utilis.get_container_nos_by_transaction_type(None))

    def test_helper_uses_ifnull_normal_pattern(self):
        src = inspect.getsource(mhr_utilis.get_container_nos_by_transaction_type)
        self.assertIn(
            "IFNULL(transaction_type, 'VFY')",
            src,
            "Shared helper must IFNULL(transaction_type, 'VFY') — otherwise legacy "
            "Containers vanish from VFY-mode view of every report.",
        )

    def test_helper_only_submitted(self):
        src = inspect.getsource(mhr_utilis.get_container_nos_by_transaction_type)
        self.assertIn(
            "docstatus = 1", src,
            "Shared helper must only consider submitted Containers (docstatus=1).",
        )

    def test_filter_rows_pass_through_when_blank(self):
        rows = [{"Container Number": "X", "qty": 1}, {"Container Number": "Y", "qty": 2}]
        self.assertEqual(
            mhr_utilis.filter_rows_by_transaction_type(rows, {}, "Container Number"),
            rows,
        )
        self.assertEqual(
            mhr_utilis.filter_rows_by_transaction_type(
                rows, {"transaction_type": ""}, "Container Number"
            ),
            rows,
        )

    def test_filter_rows_uses_container_field_arg(self):
        # Different reports use different row keys ("Container Number" vs
        # "Container No"). The helper must consult the per-report key.
        src = inspect.getsource(mhr_utilis.filter_rows_by_transaction_type)
        self.assertIn("container_field", src,
            "filter_rows_by_transaction_type must accept a `container_field` arg "
            "because different reports key the Container value differently.")


class TestContainerReportTransactionTypeFilter(FrappeTestCase):
    """Container Report — post-aggregate Python filter via shared helper."""

    def test_blank_filter_is_noop(self):
        """Blank transaction_type must NOT filter — full result set returned."""
        cols_a, rows_a = cr.execute({})
        cols_b, rows_b = cr.execute({"transaction_type": ""})
        self.assertEqual(len(rows_a), len(rows_b),
            "Empty transaction_type filter must behave the same as no filter at all.")

    def test_execute_uses_shared_helper(self):
        src = inspect.getsource(cr.execute)
        self.assertIn(
            "filter_rows_by_transaction_type",
            src,
            "Container Report must use the shared helper "
            "(mhr.utilis.filter_rows_by_transaction_type) — DRY across all 6 reports.",
        )
        self.assertIn(
            'container_field="container_number"',
            src,
            "Container Report rows key the value under 'container_number'.",
        )


class TestStockSheetReportsTransactionTypeFilter(FrappeTestCase):
    """The stock_sheet reports must also use the shared helper with the
    right per-report container_field key. Different reports use
    'Container Number' vs 'Container No' — getting that key wrong
    silently breaks the filter (returns 0 rows for HTY, all rows for
    VFY).

    MI1-I99 (2026-08-17): STOCK SHEET (BALANCE REPORT) is deliberately NOT in
    this list any more. It is the only one of these reports that emits its own
    subtotal and grand-total rows, and those rows carry a blank
    'Container Number' — so the post-aggregate helper deleted every one of them
    the moment a Transaction Type was picked, and the totals it did produce had
    been summed before the filter ran. That report now restricts its batch query
    up front instead; see TestBalanceReportFiltersAtQueryLevel below."""

    # (module_path, expected_container_field)
    WIRINGS = [
        ("mhr.mhr.report.stock_sheet_(balance_report_simple).stock_sheet_(balance_report_simple)", "Container Number"),
        ("mhr.mhr.report.stock_sheet_(inward_cone_wise).stock_sheet_(inward_cone_wise)",     "Container Number"),
        ("mhr.mhr.report.stock_sheets_(inward_coneless_stock_).stock_sheets_(inward_coneless_stock_)", "Container No"),
        ("mhr.mhr.report.stock_sheets_(inward_rest_stock_).stock_sheets_(inward_rest_stock_)",        "Container No"),
    ]

    def test_each_report_uses_shared_helper_with_correct_key(self):
        import importlib
        for modpath, expected_field in self.WIRINGS:
            with self.subTest(module=modpath):
                mod = importlib.import_module(modpath)
                src = inspect.getsource(mod.execute)
                self.assertIn(
                    "filter_rows_by_transaction_type",
                    src,
                    f"{modpath} must call the shared helper from mhr.utilis.",
                )
                self.assertIn(
                    f'container_field="{expected_field}"',
                    src,
                    f"{modpath} must pass container_field={expected_field!r} — "
                    "the row dict uses this exact key.",
                )


class TestBalanceReportFiltersAtQueryLevel(FrappeTestCase):
    """MI1-I99 — STOCK SHEET (BALANCE REPORT) filters transaction_type on the
    batch query, not on the finished rows, so its lot / container / grand
    totals are summed from the filtered set and survive the filter."""

    MODPATH = "mhr.mhr.report.stock_sheet_(balance_report).stock_sheet_(balance_report)"

    def setUp(self):
        import importlib

        self.mod = importlib.import_module(self.MODPATH)

    def test_execute_does_not_post_filter_rows(self):
        """The post-aggregate helper drops every row with a blank container —
        which is exactly what the total rows are."""
        src = inspect.getsource(self.mod.execute)
        self.assertNotIn(
            "data = filter_rows_by_transaction_type(",
            src,
            "The balance report must not post-filter its rows — that deletes "
            "the Grand Total and every subtotal.",
        )

    def test_get_data_restricts_by_transaction_type(self):
        src = inspect.getsource(self.mod.get_data)
        self.assertIn(
            "get_container_nos_by_transaction_type",
            src,
            "get_data must narrow the batch query by transaction_type so the "
            "totals describe the filtered set.",
        )
        self.assertIn(
            "custom_container_no.isin",
            src,
            "The transaction_type restriction belongs on the batch query.",
        )

    def test_blank_filter_is_noop(self):
        cols_a, rows_a = self.mod.execute({})
        cols_b, rows_b = self.mod.execute({"transaction_type": ""})
        self.assertEqual(
            len(rows_a), len(rows_b),
            "Empty transaction_type must behave exactly like no filter.",
        )

    def test_grand_total_survives_transaction_type_filter(self):
        for tt in ("VFY", "HTY"):
            with self.subTest(transaction_type=tt):
                _cols, rows = self.mod.execute({"transaction_type": tt})
                if not rows:
                    self.skipTest(f"No {tt} stock on this site.")
                totals = [r for r in rows if r.get("sort_order") == 3]
                self.assertEqual(
                    len(totals), 1,
                    f"Exactly one Grand Total row must survive a {tt} filter.",
                )

    def test_grand_total_matches_the_filtered_detail_rows(self):
        """The whole point of MI1-I99: totals describe what is on screen."""
        for tt in ("", "VFY", "HTY"):
            with self.subTest(transaction_type=tt):
                _cols, rows = self.mod.execute({"transaction_type": tt} if tt else {})
                totals = [r for r in rows if r.get("sort_order") == 3]
                if not rows or not totals:
                    self.skipTest(f"No rows for transaction_type={tt!r}.")
                grand = totals[0]

                # Detail rows repeat Balance once per Sales Order allocation
                # (MI1-I94), so reconcile on unique group keys, not raw sums.
                seen = set()
                expected_boxes = 0
                for r in rows:
                    if r.get("sort_order"):
                        continue
                    key = (r.get("Container Number"), r.get("Lot Number"), r.get("Cone"))
                    if key in seen:
                        continue
                    seen.add(key)
                    expected_boxes += int(r.get("Balance Box") or 0)
                self.assertEqual(
                    int(grand.get("Balance Box") or 0), expected_boxes,
                    "Grand Total Balance Box must equal the filtered detail rows.",
                )

    def test_all_qty_columns_are_totalled(self):
        _cols, rows = self.mod.execute({})
        totals = [r for r in rows if r.get("sort_order") == 3]
        if not totals:
            self.skipTest("No stock on this site.")
        grand = totals[0]
        # Labels are crossed over: "Booked Qty" is fieldname `Buyer Qty`,
        # "Total Booked" is fieldname `Booked Qty`.
        for fieldname, label in (
            ("Balance", "Balance Qty"),
            ("Buyer Qty", "Booked Qty"),
            ("Balance Box", "Balance Box"),
            ("Booked Qty", "Total Booked"),
            ("Available Qty", "Available Qty"),
        ):
            with self.subTest(column=label):
                self.assertNotEqual(
                    grand.get(fieldname), "",
                    f"Grand Total must carry a value for the {label!r} column "
                    f"(fieldname {fieldname!r}).",
                )
                self.assertIsNotNone(grand.get(fieldname))


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
            "IFNULL(dn.transaction_type, 'VFY')",
            src,
            "Delivery Challan WHERE must IFNULL(dn.transaction_type, 'VFY') so legacy "
            "DNs appear under VFY — matches FRD's 'VFY = unchanged' rule.",
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
