"""Reopen-batch tests for MI1-I26, MI1-I27, MI1-I28.

After the original fixes shipped, Raj reopened each ticket because the
day-to-day flow he actually uses still showed the same symptom:

  MI1-I26  Stock Entry Submit timeout: original fix added a "Submit in
           Background" button but user kept clicking the standard Submit
           and got "Request Timed Out". Follow-up: block the standard
           Submit on large transfers and surface a banner pointing at
           the background button.

  MI1-I27  Print Batch Lot No empty when reopening a saved form: original
           fix made get_print_batch return a list. Follow-up: the lot_no
           Select options weren't being repopulated on `refresh` — only
           on `container_no` change — so re-opening an existing doc
           left the dropdown empty.

  MI1-I28  Original fix split rows in the Delivery Challan report. User
           reopened because the report he actually uses is the
           Delivery Note list view ("DN"), not the Delivery Challan
           report. Follow-up: brand-new "Delivery Note Lot-Wise" Script
           Report that gives him the lot-split view he wants.
"""
import inspect
import frappe
from frappe.tests.utils import FrappeTestCase


class TestStockEntryLargeSubmitGuard(FrappeTestCase):
    """MI1-I26 reopen — the stock_entry.js bundle must block the standard
    Submit on large Material Transfers and point users to the background
    button instead."""

    def setUp(self):
        from pathlib import Path
        js_path = Path("/home/frappe/frappe-bench/apps/mhr/mhr/public/js/stock_entry.js")
        self.script = js_path.read_text()

    def test_threshold_constant_present(self):
        self.assertIn(
            "MI1_I26_LARGE_SE_THRESHOLD", self.script,
            "Must define a named threshold so it's easy to bump without "
            "hunting through magic numbers.",
        )

    def test_before_submit_handler_blocks_large_transfers(self):
        self.assertIn(
            "before_submit(frm)", self.script,
            "Must hook before_submit to intercept the standard Submit.",
        )
        self.assertIn(
            "frappe.throw", self.script,
            "Must frappe.throw on large transfers so the submit aborts cleanly.",
        )
        self.assertIn(
            "Submit in Background", self.script,
            "Throw message must reference the 'Submit in Background' "
            "button so the user knows which button to click.",
        )

    def test_banner_on_refresh_for_large_drafts(self):
        self.assertIn(
            "frm.dashboard.add_comment", self.script,
            "Must surface a dashboard banner on draft refresh for large "
            "Stock Entries so the user sees the hint BEFORE clicking Submit.",
        )


class TestPrintBatchRefreshRepopulatesLotNos(FrappeTestCase):
    """MI1-I27 reopen — when a saved Print Batch is re-opened, the
    lot_no dropdown must show available lots, not be empty."""

    def setUp(self):
        from pathlib import Path
        js_path = Path(
            "/home/frappe/frappe-bench/apps/mhr/mhr/mhr/doctype/print_batch/print_batch.js"
        )
        self.script = js_path.read_text()

    def test_refresh_handler_present(self):
        self.assertIn(
            "refresh: function(frm)", self.script,
            "Print Batch JS must define a refresh handler so saved docs "
            "get the lot_no options repopulated.",
        )

    def test_refresh_calls_helper_with_preserve_value(self):
        self.assertIn(
            "mi1_i27_populate_lot_nos(frm, /* preserve_value */ true)", self.script,
            "refresh must call the helper with preserve_value=true so the "
            "user's existing lot_no selection survives.",
        )

    def test_container_no_handler_calls_helper(self):
        self.assertIn(
            "mi1_i27_populate_lot_nos(frm, /* preserve_value */ false)", self.script,
            "container_no change must repopulate options with preserve_value=false.",
        )

    def test_helper_function_defined(self):
        self.assertIn(
            "function mi1_i27_populate_lot_nos(", self.script,
            "Shared helper must be defined.",
        )


class TestDeliveryNoteLotWiseReport(FrappeTestCase):
    """MI1-I28 reopen — new Delivery Note Lot-Wise Script Report."""

    def test_report_registered(self):
        self.assertTrue(
            frappe.db.exists("Report", "Delivery Note Lot-Wise"),
            "Report must be registered in DB.",
        )

    def test_report_meta(self):
        d = frappe.db.get_value(
            "Report", "Delivery Note Lot-Wise",
            ["ref_doctype", "module", "is_standard", "report_type"],
            as_dict=True,
        )
        self.assertEqual(d.ref_doctype, "Delivery Note")
        self.assertEqual(d.module, "Mhr")
        self.assertEqual(d.is_standard, "Yes")
        self.assertEqual(d.report_type, "Script Report")

    def test_columns_match_user_screenshot(self):
        from mhr.mhr.report.delivery_note_lot_wise import delivery_note_lot_wise as rep
        cols = rep.get_columns()
        names = [c["fieldname"] for c in cols]
        # The user's screenshot showed: Status, ID, Challan, Date, Denier,
        # Pulp, Glue, Lusture, Grade, Total Qty, Merge No, Lot No, Item Length.
        # We also include container_no + customer for context.
        for required in (
            "status", "name", "challan", "posting_date",
            "denier", "pulp", "glue", "lusture", "grade",
            "container_no", "lot_no", "total_qty",
            "merge_no", "item_length", "customer",
        ):
            self.assertIn(
                required, names,
                f"Column {required!r} must be present — Raj's screenshot lists it.",
            )

    def test_splits_one_row_per_lot(self):
        """Source-level check that GROUP BY includes lot_no — that's
        what produces the row-per-lot behavior the user wants."""
        from mhr.mhr.report.delivery_note_lot_wise import delivery_note_lot_wise as rep
        src = inspect.getsource(rep.get_data)
        self.assertIn(
            "GROUP BY dn.name, dni.custom_container_no, dni.custom_lot_no",
            src,
            "GROUP BY must split by lot — that's the whole point of this report.",
        )

    def test_empty_range_smoke(self):
        from mhr.mhr.report.delivery_note_lot_wise import delivery_note_lot_wise as rep
        cols, rows = rep.execute({"from_date": "1900-01-01", "to_date": "1900-01-02"})
        self.assertGreater(len(cols), 0)
        self.assertEqual(rows, [])
