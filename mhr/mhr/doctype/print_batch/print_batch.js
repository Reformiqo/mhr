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

    refresh: function(frm) {
        // MI1-I27 reopen: when re-opening a saved Print Batch, the
        // container_no change handler doesn't fire so the lot_no Select
        // options stay empty — user can't pick a lot even though one
        // is clearly available. Repopulate options on every refresh
        // whenever container_no is set, preserving the existing lot_no
        // selection.
        if (frm.doc.container_no) {
            mi1_i27_populate_lot_nos(frm, /* preserve_value */ true);
        }
        // MI1-I27 (Item bifurcation): also repopulate the Item Select on
        // reopen so a saved Print Batch keeps a usable Item dropdown.
        if (frm.doc.container_no && frm.doc.lot_no) {
            mi1_i27_populate_items(frm, /* preserve_value */ true);
        }
    },

    container_no: function(frm) {
        // Clear lot_no + item when container_no changes
        frm.set_value('lot_no', '');
        frm.set_value('item', '');
        frm.set_df_property('item', 'options', '');
        frm.refresh_field('item');
        if (frm.doc.container_no) {
            mi1_i27_populate_lot_nos(frm, /* preserve_value */ false);
        } else {
            // Clear lot_no options if container_no is cleared
            frm.set_df_property('lot_no', 'options', '');
            frm.refresh_field('lot_no');
        }
    },

    supplier_batch_no: function(frm) {
        // The Data field's change event fires while the user is still
        // typing the supplier batch no (e.g. "4" -> "48" -> "480" ->
        // "4804"). Each partial value hit the server and popped the
        // "No batches found" message. Debounce so we only fetch once the
        // user has paused typing (or moved on). The full value still
        // works exactly as before — it just no longer fires mid-type.
        if (frm._sbn_debounce) clearTimeout(frm._sbn_debounce);
        frm._sbn_debounce = setTimeout(function() {
            frm._sbn_debounce = null;
            fetch_and_append_batch(frm);
        }, 2000);
    },

    lot_no: function(frm) {
        // MI1-I27 (Item bifurcation): a new lot may hold a different set
        // of items — reset + repopulate the Item Select.
        frm.set_value('item', '');
        if (frm.doc.container_no && frm.doc.lot_no) {
            mi1_i27_populate_items(frm, /* preserve_value */ false);
        } else {
            frm.set_df_property('item', 'options', '');
            frm.refresh_field('item');
        }
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
            // MI1-I27 (Item bifurcation): when set, only this item's
            // batches are returned; blank = all items (old behaviour).
            item: frm.doc.item || "",
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

            // Build the dedup lookup once (O(n)) instead of re-scanning the
            // whole child table for every returned row (O(n*m)). list_batches
            // can hold up to 1000 rows, so this matters. Adding to the Set as
            // we go also dedups duplicates within the same payload.
            var existing = new Set((frm.doc.list_batches || []).map(function(row) {
                return row.batch;
            }));

            var added = 0;
            var skipped = 0;
            rows.forEach(function(data) {
                if (!data || !data.batch) return;
                if (existing.has(data.batch)) { skipped++; return; }
                existing.add(data.batch);
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

// MI1-I27 reopen: shared helper called from both `refresh` (preserve
// existing lot_no) and `container_no` change (let the new options
// drive selection).
function mi1_i27_populate_lot_nos(frm, preserve_value) {
    var prev = frm.doc.lot_no;
    frm.call({
        method: "get_lot_nos",
        args: { container_no: frm.doc.container_no },
        callback: function(response) {
            var lot_nos = response.message || [];
            var options = [''].concat(lot_nos);
            frm.set_df_property('lot_no', 'options', options.join('\n'));
            frm.refresh_field('lot_no');
            if (preserve_value && prev) {
                // refresh_field may have cleared the value if it's not
                // in the new options list — restore it if still valid.
                if (lot_nos.indexOf(prev) >= 0) {
                    frm.set_value('lot_no', prev);
                }
            }
        }
    });
}

// MI1-I27 (Item bifurcation): populate the Item Select with the distinct
// items present for the current Container + Lot No. Mirrors the lot_no
// helper above — first option is blank ("all items").
function mi1_i27_populate_items(frm, preserve_value) {
    var prev = frm.doc.item;
    frm.call({
        method: "get_items",
        args: { container_no: frm.doc.container_no, lot_no: frm.doc.lot_no },
        callback: function(response) {
            var items = response.message || [];
            var options = [''].concat(items);
            frm.set_df_property('item', 'options', options.join('\n'));
            frm.refresh_field('item');
            if (preserve_value && prev && items.indexOf(prev) >= 0) {
                frm.set_value('item', prev);
            }
        }
    });
}

