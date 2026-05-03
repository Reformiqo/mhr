"""Regression tests for Delivery Challan report — MI1-I24 + MI1-I28.

Raj Tiwari asked for:
  I24 — a Remark column at the end of the Delivery Challan report.
  I28 — when one Delivery Note carries two lots from the same
        container, show ONE ROW PER LOT, not a single combined row.

Both changes live in
`mhr.mhr.report.delivery_challan.delivery_challan`. Tests pin:
  - Columns include Remark and the new Container No / Lot No columns.
  - SQL groups by (dn.name, container_no, lot_no) so a multi-lot DN
    produces multiple rows.
"""
import inspect
import re

import frappe
from frappe.tests.utils import FrappeTestCase


class TestDeliveryChallanReport(FrappeTestCase):

    def test_columns_include_remark(self):
        from mhr.mhr.report.delivery_challan.delivery_challan import get_columns
        cols = get_columns()
        labels = {c["label"] for c in cols}
        self.assertIn(
            "Remark", labels,
            "MI1-I24: Delivery Challan report must include a Remark column.",
        )

    def test_columns_include_container_and_lot(self):
        from mhr.mhr.report.delivery_challan.delivery_challan import get_columns
        cols = get_columns()
        labels = {c["label"] for c in cols}
        self.assertIn("Container No", labels, "MI1-I28: Container No column missing.")
        self.assertIn("Lot No", labels, "MI1-I28: Lot No column missing.")

    def test_remark_column_is_last(self):
        """Per Raj's screenshot the Remark column should appear at the
        end of the existing layout."""
        from mhr.mhr.report.delivery_challan.delivery_challan import get_columns
        cols = get_columns()
        self.assertEqual(
            cols[-1]["label"], "Remark",
            "Remark column must be the last column.",
        )

    def test_query_groups_by_container_lot(self):
        """The SQL must GROUP BY container_no + lot_no so multi-lot
        DNs split into multiple rows."""
        import mhr.mhr.report.delivery_challan.delivery_challan as mod
        src = inspect.getsource(mod.get_data)
        # Strip line comments only — the SQL lives inside an f-string,
        # so we can't naively strip triple-quoted blocks.
        no_line = re.sub(r"#[^\n]*", "", src)
        self.assertIn(
            "custom_container_no", no_line,
            "MI1-I28: SQL must reference dni.custom_container_no.",
        )
        self.assertIn(
            "custom_lot_no", no_line,
            "MI1-I28: SQL must reference dni.custom_lot_no.",
        )
        # GROUP BY must include lot_no, otherwise rows still collapse
        m = re.search(r"GROUP\s+BY[^\n]+", no_line, re.IGNORECASE)
        self.assertIsNotNone(m, "no GROUP BY clause found")
        gb = m.group(0).lower()
        self.assertIn("custom_lot_no", gb, "GROUP BY must include custom_lot_no.")
        self.assertIn("custom_container_no", gb, "GROUP BY must include custom_container_no.")

    def test_query_pulls_remark_field(self):
        import mhr.mhr.report.delivery_challan.delivery_challan as mod
        src = inspect.getsource(mod.get_data)
        no_line = re.sub(r"#[^\n]*", "", src)
        self.assertIn(
            "custom_notes", no_line,
            "MI1-I24: SQL must select `custom_notes` for the Remark column. "
            "If the customer keeps the remark in a different field, swap the source.",
        )

    def test_executes_against_empty_filters_safely(self):
        """Sanity: report shouldn't crash on a fresh test bench with
        empty data. Frappe.throw on missing date filters is expected;
        a wider crash is not."""
        from mhr.mhr.report.delivery_challan.delivery_challan import execute
        with self.assertRaises(frappe.exceptions.ValidationError):
            execute({})  # no from_date / to_date → throw is expected
