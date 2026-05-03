frappe.ui.form.on('Print Batch', {
    setup: function(frm) {
        // Add a flag to ensure the PDF is opened only once
        if (!frm.is_opening_pdf) {
            frm.is_opening_pdf = true;

            // Listen for the real-time event
            frappe.realtime.on('pdf_generated', function(data) {
                if (data && data.file_url) {
                    // Open the generated PDF
                    window.open(data.file_url, '_blank');
                    // Reset the flag after opening the PDF
                    frm.is_opening_pdf = false;
                }
            });
        }
    },

    container_no: function(frm) {
        // Clear lot_no when container_no changes
        frm.set_value('lot_no', '');

        if (frm.doc.container_no) {
            // Fetch lot numbers for the selected container
            frm.call({
                method: "get_lot_nos",
                args: {
                    container_no: frm.doc.container_no
                },
                callback: function(response) {
                    if (response.message) {
                        var lot_nos = response.message;
                        // Build options string with empty first option
                        var options = [''].concat(lot_nos);
                        frm.set_df_property('lot_no', 'options', options.join('\n'));
                        frm.refresh_field('lot_no');
                    }
                }
            });
        } else {
            // Clear lot_no options if container_no is cleared
            frm.set_df_property('lot_no', 'options', '');
            frm.refresh_field('lot_no');
        }
    },

    supplier_batch_no: function(frm) {
        fetch_and_append_batch(frm);
    },

    lot_no: function(frm) {
        fetch_and_append_batch(frm);
    }
});

function fetch_and_append_batch(frm) {
    if (!frm.doc.supplier_batch_no) return;

    frappe.call({
        method: "mhr.utilis.get_print_batch",
        args: {
            lot_no: frm.doc.lot_no,
            container_no: frm.doc.container_no,
            supplier_batch_no: frm.doc.supplier_batch_no,
        },
        callback: function(response) {
            // MI1-I27: server now returns an ARRAY of Batches matching
            // (container, lot, supplier_batch_no). Same trio can map
            // to multiple Batches (different deniers / items) — append
            // one row per Batch instead of just the first.
            var rows = response.message || [];
            if (!Array.isArray(rows)) rows = [rows]; // back-compat for older payloads
            if (rows.length === 0) {
                frappe.msgprint(__('No batches found for that container / lot / supplier batch.'));
                return;
            }

            var added = 0;
            var skipped = 0;
            rows.forEach(function(data) {
                if (!data || !data.batch) return;
                var exists = frm.doc.list_batches.some(function(row) {
                    return row.batch === data.batch;
                });
                if (exists) { skipped++; return; }
                var childTable = frm.add_child("list_batches");
                childTable.batch = data.batch;
                childTable.cone = data.cone;
                childTable.lot_no = data.lot_no;
                childTable.batch_qty = data.batch_qty;
                added++;
            });

            if (added > 0) {
                frm.refresh_field("list_batches");
                frm.set_value("supplier_batch_no", "");
                if (rows.length > 1) {
                    frappe.show_alert({
                        message: __('Added {0} batch(es) for that supplier batch.', [added]),
                        indicator: 'green',
                    }, 4);
                }
            } else if (skipped > 0) {
                frappe.msgprint(__('All matching batches are already in the list.'));
            }
        }
    });
}

frappe.ui.form.on('List Batches', {
    batch: function(frm, cdt, cdn) {
        var child = locals[cdt][cdn];
        // Check if the batch already exists in the child table
        var exists = frm.doc.list_batches.some(function(row) {
            return row.batch === child.batch && row.name !== child.name;
        });

        if (exists) {
            frappe.msgprint(__('Batch already exists in the list.'));
            frappe.model.set_value(cdt, cdn, 'batch', '');
        }
    }
});
