// MI1-I69 (2026-06-23): DN is now a Script Report (was Query Report).
// Column labels swap with the Transaction Type filter server-side via
// dn.py's get_columns(filters), so no JS hack is needed for the rename.

frappe.query_reports["DN"] = {
    "filters": [
        // Raj 2026-06-24: no default date — report must stay blank
        // until the user picks both From + To. reqd:0 + no default
        // gets us that; the Python execute() returns [] when either
        // date is missing, so an accidental run with one date set
        // still surfaces nothing.
        {
            "fieldname": "from_date",
            "label": __("From Date"),
            "fieldtype": "Date",
            "reqd": 0,
        },
        {
            "fieldname": "to_date",
            "label": __("To Date"),
            "fieldtype": "Date",
            "reqd": 0,
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
