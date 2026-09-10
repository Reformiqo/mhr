"""MI1-I131 (Raj 2026-09-10) — "The Stock Ledger Report is not getting
generated."

Root cause: ERPNext's standard "Stock Ledger" report is a Script Report, and
frappe flips `Report.prepared_report` to 1 on its own the first time a run
takes longer than 15 s (frappe.core.doctype.report.report ::
enable_prepared_report) — the exact mechanism already documented for mhr's
own Stock Sheet (Balance Report), MI1-I119. This site's Stock Ledger record
was flipped that way on 2025-05-30 (modified_by Administrator — the
watcher's background thread, never a deliberate admin choice), so every open
of the report since showed "This is a background report..." instead of
running, however complete the filters were.

Two parts, matching the balance report's precedent:
  1. A one-time patch resets the flag now.
  2. An hourly scheduler event resets it again if a future slow run flips it
     back — Stock Ledger's own execute() lives in ERPNext, not mhr, so it
     cannot be given the self-healing "keep_inline" check embedded in a
     single run the way the balance report has; the balance report's own
     patch needed manual re-registration on 2026-09-08 after one slow run
     undid it, which is exactly the flapping this hourly job prevents here.
"""
import os

import frappe
from frappe.tests.utils import FrappeTestCase

from mhr import utilis
from mhr.patches.v1_0 import set_stock_ledger_report_inline

REPORT = "Stock Ledger"


class TestPatchResetsTheFlag(FrappeTestCase):

    def setUp(self):
        self._original = frappe.db.get_value("Report", REPORT, "prepared_report")
        self._original_modified = frappe.db.get_value("Report", REPORT, "modified")

    def tearDown(self):
        frappe.db.set_value("Report", REPORT, "prepared_report", self._original, update_modified=False)

    def test_flips_prepared_report_off(self):
        frappe.db.set_value("Report", REPORT, "prepared_report", 1, update_modified=False)
        set_stock_ledger_report_inline.execute()
        self.assertEqual(frappe.db.get_value("Report", REPORT, "prepared_report"), 0)

    def test_never_bumps_modified(self):
        """A background-thread flag reset must not look like a fresh edit —
        matches update_modified=False on the balance report's own patch."""
        frappe.db.set_value("Report", REPORT, "prepared_report", 1, update_modified=False)
        set_stock_ledger_report_inline.execute()
        self.assertEqual(frappe.db.get_value("Report", REPORT, "modified"), self._original_modified)

    def test_no_op_when_report_missing(self):
        """Guard mirrors set_stock_sheet_balance_report_inline: a site
        without this report (or a renamed one) must not error on migrate."""
        self.assertFalse(frappe.db.exists("Report", "MI1-I131-No-Such-Report"))
        original = set_stock_ledger_report_inline.REPORT
        set_stock_ledger_report_inline.REPORT = "MI1-I131-No-Such-Report"
        try:
            set_stock_ledger_report_inline.execute()  # must not raise
        finally:
            set_stock_ledger_report_inline.REPORT = original


class TestHourlySelfHeal(FrappeTestCase):

    def setUp(self):
        self._original = frappe.db.get_value("Report", REPORT, "prepared_report")

    def tearDown(self):
        frappe.db.set_value("Report", REPORT, "prepared_report", self._original, update_modified=False)

    def test_resets_when_flipped(self):
        frappe.db.set_value("Report", REPORT, "prepared_report", 1, update_modified=False)
        utilis.keep_core_reports_inline()
        self.assertEqual(frappe.db.get_value("Report", REPORT, "prepared_report"), 0)

    def test_is_a_no_op_when_already_inline(self):
        frappe.db.set_value("Report", REPORT, "prepared_report", 0, update_modified=False)
        modified_before = frappe.db.get_value("Report", REPORT, "modified")
        utilis.keep_core_reports_inline()
        self.assertEqual(frappe.db.get_value("Report", REPORT, "modified"), modified_before)

    def test_stock_ledger_is_the_report_covered(self):
        self.assertIn(REPORT, utilis.CORE_REPORTS_TO_KEEP_INLINE)

    def test_a_missing_report_does_not_raise(self):
        utilis.CORE_REPORTS_TO_KEEP_INLINE = ("MI1-I131-No-Such-Report",)
        try:
            utilis.keep_core_reports_inline()  # must not raise
        finally:
            utilis.CORE_REPORTS_TO_KEEP_INLINE = (REPORT,)


class TestWiring(FrappeTestCase):

    def test_patch_is_registered(self):
        with open(os.path.join(frappe.get_app_path("mhr"), "patches.txt")) as f:
            self.assertIn("mhr.patches.v1_0.set_stock_ledger_report_inline", f.read())

    def test_hourly_scheduler_event_is_registered(self):
        import mhr.hooks as hooks
        self.assertIn("mhr.utilis.keep_core_reports_inline", hooks.scheduler_events["hourly"])

    def test_stock_ledger_is_actually_a_script_report_on_this_bench(self):
        """Pins the precondition the whole fix rests on: the 15s watcher
        (execute_script_report) only ever fires for Script Reports."""
        self.assertEqual(frappe.db.get_value("Report", REPORT, "report_type"), "Script Report")
