import frappe

REPORT = "Stock Ledger"


def execute():
    """MI1-I131: run the standard ERPNext "Stock Ledger" report inline again.

    Same mechanism already documented for Stock Sheet (Balance Report),
    MI1-I119 — frappe flips `Report.prepared_report` to 1 on its own the
    first time a run takes longer than 15 s
    (frappe.core.doctype.report.report :: enable_prepared_report). This site's
    "Stock Ledger" Report record was flipped that way on 2025-05-30
    (modified_by Administrator, matching the watcher's background thread, not
    a deliberate admin choice). Every run since has shown "This is a
    background report. Please set the appropriate filters and then generate
    a new one." instead of the requested data — Raj reported this as "the
    Stock Ledger Report is not getting generated."

    This is core ERPNext's own report — its execute() cannot be given the
    self-healing "keep_inline" check the mhr-owned reports have, so
    `mhr.utilis.keep_core_reports_inline` (hourly scheduler event) resets the
    flag again if a future slow run flips it back, the way
    set_stock_sheet_balance_report_inline needed re-registering on
    2026-09-08 after a single patch run alone was not durable.
    """
    if not frappe.db.exists("Report", REPORT):
        return
    frappe.db.set_value("Report", REPORT, "prepared_report", 0, update_modified=False)
