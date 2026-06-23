// MI1-I69 (2026-06-23): DN is now a Script Report (was Query Report).
// Column labels swap with the Transaction Type filter server-side via
// dn.py's get_columns(filters), so no JS hack is needed for the rename.

frappe.query_reports["DN"] = {
    "filters": [
        {
            "fieldname": "from_date",
            "label": __("From Date"),
            "fieldtype": "Date",
            "default": frappe.datetime.month_start(),
            "reqd": 1,
        },
        {
            "fieldname": "to_date",
            "label": __("To Date"),
            "fieldtype": "Date",
            "default": frappe.datetime.get_today(),
            "reqd": 1,
        },
        {
            "fieldname": "transaction_type",
            "label": __("Transaction Type"),
            "fieldtype": "Select",
            "options": "All\nVFY\nHTY",
            "default": "All",
            on_change: function () {
                frappe.query_report.refresh();
            },
        },
    ],
};
