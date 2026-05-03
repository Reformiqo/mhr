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

frappe.ui.form.on("Stock Entry", {
    refresh(frm) {
        if (frm.doc.docstatus !== 0) return;
        if (!frm.doc.name || frm.is_new()) return;

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
