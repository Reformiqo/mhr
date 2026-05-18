"""MI1-I39 Phase 2D — HTY Master Report tests.

Brand-new Script Report per FRD §REPORT 6. Tests pin:
  - Filter validation (date range mandatory + ordered)
  - Column structure (11 columns in the exact order the FRD lists)
  - Empty-range execute returns (columns, []) without error
  - HTY semantic mapping: colour↔lusture, product↔glue, type↔pulp
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from mhr.mhr.report.hty_master_report import hty_master_report as report


EXPECTED_COLUMN_ORDER = [
    "date",
    "container_no",
    "item",
    "type",
    "colour",
    "product",
    "grade",
    "lot_no",
    "in_qty",
    "out_qty",
    "closing_stock",
]


class TestHTYMasterReportFilters(FrappeTestCase):
    """Filter validation per FRD: from_date + to_date both mandatory."""

    def test_missing_from_date_raises(self):
        with self.assertRaises(frappe.ValidationError) as ctx:
            report.execute({"to_date": "2026-05-31"})
        self.assertIn("Date From", str(ctx.exception))

    def test_missing_to_date_raises(self):
        with self.assertRaises(frappe.ValidationError) as ctx:
            report.execute({"from_date": "2026-05-01"})
        self.assertIn("Date To", str(ctx.exception))

    def test_swapped_date_range_raises(self):
        with self.assertRaises(frappe.ValidationError) as ctx:
            report.execute({"from_date": "2026-05-31", "to_date": "2026-05-01"})
        self.assertIn("Date From", str(ctx.exception))
        self.assertIn("Date To", str(ctx.exception))


class TestHTYMasterReportColumns(FrappeTestCase):
    """11 columns in the FRD's order — pinning so regressions stay loud."""

    def test_column_count(self):
        cols = report.get_columns()
        self.assertEqual(len(cols), 11)

    def test_column_order_matches_frd(self):
        cols = report.get_columns()
        actual = [c["fieldname"] for c in cols]
        self.assertEqual(actual, EXPECTED_COLUMN_ORDER)

    def test_required_link_columns(self):
        cols = {c["fieldname"]: c for c in report.get_columns()}
        self.assertEqual(cols["container_no"]["fieldtype"], "Link")
        self.assertEqual(cols["container_no"]["options"], "Container")
        self.assertEqual(cols["item"]["fieldtype"], "Link")
        self.assertEqual(cols["item"]["options"], "Item")


class TestHTYMasterReportExecute(FrappeTestCase):
    """Empty-range smoke test — execute returns (columns, []) without crash."""

    def test_empty_range_returns_no_rows(self):
        # Use a fixed date range that will not overlap test fixtures.
        cols, rows = report.execute({"from_date": "1900-01-01", "to_date": "1900-01-02"})
        self.assertEqual(len(cols), 11)
        self.assertEqual(rows, [])

    def test_returns_tuple_of_columns_and_data(self):
        out = report.execute({"from_date": "2026-05-01", "to_date": "2026-05-31"})
        self.assertEqual(len(out), 2, "execute must return (columns, data).")
        cols, rows = out
        self.assertIsInstance(cols, list)
        self.assertIsInstance(rows, list)


class TestHTYMasterReportSemanticMapping(FrappeTestCase):
    """Source-level guard: HTY column names map to Meher DB columns.
    Renaming Container.lusture/glue/pulp without updating the mapping
    would silently return empty Colour/Product/Type columns."""

    def test_row_builder_maps_hty_to_meher(self):
        import inspect
        src = inspect.getsource(report._row_from_container)
        # The mapping must be: colour <- lusture, product <- glue, type <- pulp.
        self.assertIn("c.lusture", src,
            "HTY 'Colour' column must read from Container.lusture.")
        self.assertIn("c.glue", src,
            "HTY 'Product' column must read from Container.glue.")
        self.assertIn("c.pulp", src,
            "HTY 'Type' column must read from Container.pulp.")

    def test_sle_query_filters_cancelled_and_date(self):
        import inspect
        src = inspect.getsource(report._aggregate_sle)
        self.assertIn("is_cancelled", src,
            "SLE aggregation must skip cancelled rows.")
        self.assertIn("posting_date <= ", src,
            "SLE aggregation must bound by posting_date <= to_date for closing stock.")


class TestHTYMasterReportChunking(FrappeTestCase):
    """The SLE read must be chunked at 2000 per the mhr report convention
    (CLAUDE.md). A regression here would re-introduce the un-chunked
    monolithic SQL the existing 4 stock reports were rewritten away from."""

    def test_sle_chunk_size(self):
        self.assertEqual(
            report.SLE_CHUNK, 2000,
            "Mhr stock reports chunk SLE reads in 2000-batch slices — see CLAUDE.md.",
        )
