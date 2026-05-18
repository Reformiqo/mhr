// Copyright (c) 2026, reformiqo and contributors
// For license information, please see license.txt
//
// MI1-I35 — Delivery Trip Simplified report filters.

frappe.query_reports["Delivery Trip Simplified"] = {
    filters: [
        {
            fieldname: "from_date",
            label: __("From Date"),
            fieldtype: "Date",
            default: frappe.datetime.add_days(frappe.datetime.get_today(), -30),
        },
        {
            fieldname: "to_date",
            label: __("To Date"),
            fieldtype: "Date",
            default: frappe.datetime.get_today(),
        },
        {
            fieldname: "vehicle",
            label: __("Vehicle"),
            fieldtype: "Link",
            options: "Vehicle",
        },
        {
            fieldname: "driver",
            label: __("Driver"),
            fieldtype: "Link",
            options: "Driver",
        },
        {
            fieldname: "customer",
            label: __("Customer"),
            fieldtype: "Link",
            options: "Customer",
        },
    ],
};
