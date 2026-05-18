// Copyright (c) 2026, reformiqo and contributors
// For license information, please see license.txt
//
// MI1-I39 Phase 2D — HTY Master Report filter definitions.

frappe.query_reports["HTY Master Report"] = {
    filters: [
        {
            fieldname: "from_date",
            label: __("Date From"),
            fieldtype: "Date",
            default: frappe.datetime.month_start(),
            reqd: 1,
        },
        {
            fieldname: "to_date",
            label: __("Date To"),
            fieldtype: "Date",
            default: frappe.datetime.month_end(),
            reqd: 1,
        },
        {
            fieldname: "company",
            label: __("Company"),
            fieldtype: "Link",
            options: "Company",
        },
        {
            fieldname: "item",
            label: __("Item"),
            fieldtype: "Link",
            options: "Item",
        },
        {
            fieldname: "colour",
            label: __("Colour"),
            fieldtype: "Data",
            description: __("Maps to Container.lusture in DB."),
        },
        {
            fieldname: "product",
            label: __("Product"),
            fieldtype: "Data",
            description: __("Maps to Container.glue in DB."),
        },
        {
            fieldname: "type",
            label: __("Type"),
            fieldtype: "Data",
            description: __("Maps to Container.pulp in DB."),
        },
        {
            fieldname: "grade",
            label: __("Grade"),
            fieldtype: "Data",
        },
    ],
};
