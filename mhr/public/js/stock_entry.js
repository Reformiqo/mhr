// MI1-I26 — Submit in Background button.
//
// The synchronous Submit on a 245-batch Material Transfer takes
// 60+ seconds — gunicorn kills the connection and the user sees
// "Request Timeout". This button enqueues the submit on a worker so
// the HTTP layer returns immediately. A realtime event fires when
// the submit lands.
//
// We render it in Draft state, alongside the standard Submit button.
// For small Stock Entries (< 50 items) the standard button is fine;
// for larger ones, the user clicks this one.

// MI1-I26 reopen — Raj's screenshot shows the user still clicking the
// standard Submit on a large Material Transfer and getting "Request
// Timed Out". The Submit-in-Background button is there but easy to miss.
// Threshold below: if items.length exceeds this, the standard Submit is
// blocked and the user is redirected to the background path.
const MI1_I26_LARGE_SE_THRESHOLD = 50;

frappe.ui.form.on("Stock Entry", {
    before_submit(frm) {
        // Intercept the standard Submit on large transfers. Throwing here
        // aborts the save_or_submit flow and surfaces a clear message
        // pointing at the background button.
        const n = (frm.doc.items || []).length;
        if (n > MI1_I26_LARGE_SE_THRESHOLD) {
            frappe.throw({
                title: __("Use 'Submit in Background' for large transfers"),
                message: __(
                    "This Stock Entry has {0} items. The standard Submit will time " +
                    "out on Frappe Cloud's gunicorn (request limit ~60s) for batches " +
                    "this size.<br><br>" +
                    "Click <b>Submit in Background</b> (next to the Submit button) " +
                    "instead. The page returns immediately; you'll get a notification " +
                    "when the worker finishes.",
                    [n]
                ),
            });
        }
    },

    refresh(frm) {
        if (frm.doc.docstatus !== 0) return;
        if (!frm.doc.name || frm.is_new()) return;

        // Banner at the top of large drafts so the user sees the hint
        // before they even reach for the Submit button.
        const n = (frm.doc.items || []).length;
        if (n > MI1_I26_LARGE_SE_THRESHOLD) {
            frm.dashboard.add_comment(
                __(
                    "Large transfer ({0} items) — please use the <b>Submit in Background</b> " +
                    "button above. The standard Submit will time out.",
                    [n]
                ),
                "orange",
                true,
            );
        }

        frm.add_custom_button(__("Submit in Background"), function () {
            frappe.confirm(
                __(
                    "This will run the Submit in a background worker so the page won't time out. " +
                    "You'll get a notification when it lands. " +
                    "Use this for large transfers (50+ batches). Continue?"
                ),
                function () {
                    frappe.call({
                        method: "mhr.utilis.submit_stock_entry_in_background",
                        args: { name: frm.doc.name },
                        freeze: true,
                        freeze_message: __("Queueing background submit..."),
                        callback(r) {
                            if (r.message && r.message.queued) {
                                frappe.show_alert(
                                    {
                                        message: __(
                                            "Queued — you'll be notified when this Stock Entry submits."
                                        ),
                                        indicator: "blue",
                                    },
                                    7
                                );
                            }
                        },
                    });
                }
            );
        });

        // Realtime listener: when the worker finishes, reload the form.
        if (!frm.__mhr_se_listener) {
            frm.__mhr_se_listener = true;
            frappe.realtime.on("mhr_stock_entry_submitted", function (data) {
                if (data && data.name === frm.doc.name) {
                    if (data.ok) {
                        frappe.show_alert(
                            { message: __("Stock Entry submitted in background."), indicator: "green" },
                            6
                        );
                    } else {
                        frappe.msgprint({
                            title: __("Background submit failed"),
                            message: data.error || __("Unknown error"),
                            indicator: "red",
                        });
                    }
                    frm.reload_doc();
                }
            });
        }
    },
});

// MI1-I50 P2: "Receive from Subcontractor" custom button.
//
// Shown on a SUBMITTED Stock Entry whose purpose is "Send to Subcontractor"
// and which still has pending items (qty - custom_received_qty > 0).
// Click -> mhr.utilis.make_receive_from_subcontractor builds a Draft Stock
// Entry pre-filled with items, batches, lots, custom fields, and warehouses
// reversed (subcontractor -> internal). User then submits the draft to
// record the return.
frappe.ui.form.on("Stock Entry", {
    refresh(frm) {
        if (frm.doc.docstatus !== 1) return;
        if (frm.doc.purpose !== "Send to Subcontractor") return;

        // Compute pending qty client-side (the per-item custom_received_qty
        // is kept up to date by the server hook in P3).
        const has_pending = (frm.doc.items || []).some(function (it) {
            return flt(it.qty) - flt(it.custom_received_qty || 0) > 0;
        });
        if (!has_pending) return;

        frm.add_custom_button(
            __("Receive from Subcontractor"),
            function () {
                frappe.call({
                    method: "mhr.utilis.make_receive_from_subcontractor",
                    args: { source_name: frm.doc.name },
                    freeze: true,
                    freeze_message: __("Building draft Receive entry..."),
                    callback(r) {
                        if (r && r.message && r.message.name) {
                            frappe.set_route("Form", "Stock Entry", r.message.name);
                        }
                    },
                });
            },
            __("Create"),
        );
    },
});
