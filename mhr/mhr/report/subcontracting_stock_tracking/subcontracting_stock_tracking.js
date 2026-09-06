// MI1-I123 — Subcontracting Stock Tracking
//
// Filters per the FRD (sheet 5): Company + date range mandatory, the rest
// optional. Status colours per sheet 7. Delivery Note numbers are comma
// joined on the server and rendered here as one link each; the total row
// is built on the server (Sent / Pending are totalled once per Send row).

frappe.query_reports["Subcontracting Stock Tracking"] = {
    filters: [
        {
            fieldname: "company", label: __("Company"), fieldtype: "Link", options: "Company",
            default: frappe.defaults.get_user_default("Company"), reqd: 1,
        },
        {
            fieldname: "from_date", label: __("From Date"), fieldtype: "Date",
            default: frappe.datetime.month_start(), reqd: 1,
        },
        {
            fieldname: "to_date", label: __("To Date"), fieldtype: "Date",
            default: frappe.datetime.get_today(), reqd: 1,
        },
        { fieldname: "supplier", label: __("Supplier / Job Worker"), fieldtype: "Link", options: "Supplier" },
        {
            fieldname: "source_warehouse", label: __("Source Warehouse"), fieldtype: "Link", options: "Warehouse",
            get_query: () => ({ filters: { is_group: 0, company: frappe.query_report.get_filter_value("company") } }),
        },
        {
            fieldname: "subcontractor_warehouse", label: __("Subcontractor Warehouse"), fieldtype: "Link", options: "Warehouse",
            get_query: () => ({ filters: { is_group: 0, company: frappe.query_report.get_filter_value("company") } }),
        },
        {
            fieldname: "target_warehouse", label: __("Target Warehouse"), fieldtype: "Link", options: "Warehouse",
            get_query: () => ({ filters: { is_group: 0, company: frappe.query_report.get_filter_value("company") } }),
        },
        { fieldname: "sent_item", label: __("Sent Item"), fieldtype: "Link", options: "Item" },
        { fieldname: "received_item", label: __("Received / New Item"), fieldtype: "Link", options: "Item" },
        { fieldname: "container_no", label: __("Container No."), fieldtype: "Data" },
        { fieldname: "batch_no", label: __("Lot / Batch No."), fieldtype: "Link", options: "Batch" },
        {
            fieldname: "send_entry", label: __("Send Entry No."), fieldtype: "Link", options: "Stock Entry",
            get_query: () => ({ filters: { purpose: "Send to Subcontractor", docstatus: 1 } }),
        },
        {
            fieldname: "status", label: __("Status"), fieldtype: "MultiSelectList",
            get_data: function (txt) {
                const statuses = ["Fully Delivered", "Partially Delivered", "Pending",
                    "Partially Received", "Stock Available", "Fully Received"];
                return statuses
                    .filter((s) => !txt || s.toLowerCase().includes(txt.toLowerCase()))
                    .map((s) => ({ value: s, description: "" }));
            },
        },
        {
            fieldname: "business_line", label: __("Business Line"), fieldtype: "Select",
            options: ["All", "HTY", "VFY"], default: "All",
        },
        { fieldname: "only_pending", label: __("Only Pending"), fieldtype: "Check", default: 0 },
        { fieldname: "only_undelivered", label: __("Only Undelivered Stock"), fieldtype: "Check", default: 0 },
        { fieldname: "show_stock_columns", label: __("Show stock validation columns"), fieldtype: "Check", default: 0 },
        { fieldname: "expand_delivery_notes", label: __("Expand Delivery Notes"), fieldtype: "Check", default: 0 },
        {
            fieldname: "restrict_to_date_range", label: __("Restrict receipts / deliveries to date range"),
            fieldtype: "Check", default: 0,
        },
    ],

    formatter: function (value, row, column, data, default_formatter) {
        if (data && data.is_total_row) {
            if (column.fieldname === "send_entry") {
                return `<b>${__("Total")}</b>`;
            }
            if (["send_date", "delivery_note", "status", "flags", "business_line", "uom",
                 "container_no", "lot_no", "sent_container_lot"].includes(column.fieldname)) {
                return "";
            }
            if (column.fieldtype === "Link") return "";
            value = default_formatter(value, row, column, data);
            return `<b>${value}</b>`;
        }

        if (column.fieldname === "delivery_note" && value) {
            return String(value)
                .split(",")
                .map((s) => s.trim())
                .filter(Boolean)
                .map((dn) => `<a href="/app/delivery-note/${encodeURIComponent(dn)}" data-doctype="Delivery Note" data-name="${frappe.utils.escape_html(dn)}">${frappe.utils.escape_html(dn)}</a>`)
                .join(", ");
        }

        value = default_formatter(value, row, column, data);
        if (!data) return value;

        if (column.fieldname === "status" && data.status) {
            const colours = {
                "Fully Delivered": "#27ae60",
                "Partially Delivered": "#2980b9",
                "Pending": "#c0392b",
                "Partially Received": "#d35400",
                "Stock Available": "#2ecc71",
                "Fully Received": "#7f8c8d",
            };
            const colour = colours[data.status] || "inherit";
            value = `<span style="color:${colour}; font-weight:600;">${value}</span>`;
        }
        if (column.fieldname === "pending_qty" && flt(data.pending_qty) > 0) {
            value = `<span style="color:#c0392b; font-weight:600;">${value}</span>`;
        }
        if (column.fieldname === "flags" && data.flags) {
            value = `<span style="color:#d35400;">${value}</span>`;
        }
        return value;
    },
};
