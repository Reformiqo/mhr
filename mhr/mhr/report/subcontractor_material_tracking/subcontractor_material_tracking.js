// MI1-I50 P5 — Subcontractor Material Tracking
//
// Filters: from/to date (defaults to current FY), supplier, status.
// JS formatters: pending > 0 -> red, status=Fully Received -> green.

frappe.query_reports["Subcontractor Material Tracking"] = {
    "filters": [
        {
            "fieldname": "from_date",
            "label": __("From Date"),
            "fieldtype": "Date",
            "default": frappe.datetime.add_months(frappe.datetime.get_today(), -1),
            "reqd": 0,
        },
        {
            "fieldname": "to_date",
            "label": __("To Date"),
            "fieldtype": "Date",
            "default": frappe.datetime.get_today(),
            "reqd": 0,
        },
        {
            "fieldname": "supplier",
            "label": __("Supplier"),
            "fieldtype": "Link",
            "options": "Supplier",
        },
        {
            "fieldname": "status",
            "label": __("Status"),
            "fieldtype": "Select",
            "options": "\nOpen\nPartially Received\nFully Received",
        },
    ],

    "formatter": function (value, row, column, data, default_formatter) {
        value = default_formatter(value, row, column, data);
        if (!data) return value;

        if (column.fieldname === "pending_qty" && flt(data.pending_qty) > 0) {
            value = `<span style="color:#c0392b; font-weight:600;">${value}</span>`;
        }
        if (column.fieldname === "status") {
            if (data.status === "Fully Received") {
                value = `<span style="color:#27ae60; font-weight:600;">${value}</span>`;
            } else if (data.status === "Partially Received") {
                value = `<span style="color:#d35400; font-weight:600;">${value}</span>`;
            }
        }
        return value;
    },
};
