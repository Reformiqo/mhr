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
    // Ensure both fields have values before making the call
    if (frm.doc.supplier_batch_no) {
        frappe.call({
            method: "mhr.utilis.get_print_batch",
            args: {
                lot_no: frm.doc.lot_no,
                container_no: frm.doc.container_no,
                supplier_batch_no: frm.doc.supplier_batch_no,
            },
            callback: function(response) {
                if (response.message) {
                    console.log(response.message);
                    var data = response.message;

                    // Check if the batch already exists in the child table
                    var exists = frm.doc.list_batches.some(function(row) {
                        return row.batch === data.batch;
                    });

                    if (!exists) {
                        // Add a new row to the table
                        var childTable = frm.add_child("list_batches");
                        childTable.batch = data.batch;
                        childTable.cone = data.cone;
                        childTable.lot_no = data.lot_no;
                        childTable.batch_qty = data.batch_qty;
                        
                        // Move the newly added row to the top of the table
                        frm.doc.list_batches.unshift(frm.doc.list_batches.pop());
                        
                        frm.refresh_field("list_batches");
                        frm.set_value("supplier_batch_no", "");  
                    } else {
                        frappe.msgprint(__('Batch already exists in the list.'));
                    }
                }
            }
        });
    }
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
