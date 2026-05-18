// Copyright (c) 2026, reformiqo and contributors
// MI1-I28 reopen — Delivery Note Lot-Wise.

frappe.query_reports["Delivery Note Lot-Wise"] = {
    filters: [
        {
            fieldname: "from_date",
            label: __("From Date"),
            fieldtype: "Date",
            default: frappe.datetime.add_months(frappe.datetime.get_today(), -1),
        },
        {
            fieldname: "to_date",
            label: __("To Date"),
            fieldtype: "Date",
            default: frappe.datetime.get_today(),
        },
        {
            fieldname: "customer",
            label: __("Customer"),
            fieldtype: "Link",
            options: "Customer",
        },
        {
            fieldname: "delivery_note",
            label: __("Delivery Note"),
            fieldtype: "Link",
            options: "Delivery Note",
        },
        {
            fieldname: "container_no",
            label: __("Container No"),
            fieldtype: "Data",
        },
        {
            fieldname: "lot_no",
            label: __("Lot No"),
            fieldtype: "Data",
        },
    ],
};
