import frappe

REPORT = "STOCK SHEET (BALANCE REPORT)"


def execute():
    """MI1-I119: run the Stock Sheet (Balance Report) inline again.

    frappe flips `Report.prepared_report` to 1 on its own the first time a
    run takes longer than 15 s (frappe.core.doctype.report.report ::
    enable_prepared_report). A full-range run used to take ~85 s, so every
    site that ever ran one ended up in prepared-report mode: "Rebuild",
    "Generate New Report", a background job the user waits minutes for, and
    on prod the HTY run that never came back at all. The standard report JSON
    cannot undo that — `before_export` writes prepared_report 0 into the file,
    but the flag is not applied when the file is re-imported over an existing
    Report. With the balance stage ~6x faster (see
    add_serial_batch_bundle_status_index and the report module) the run fits
    the inline budget, so reset the flag once; the 15 s watcher stays as the
    safety valve.
    """
    if not frappe.db.exists("Report", REPORT):
        return
    frappe.db.set_value("Report", REPORT, "prepared_report", 0, update_modified=False)
