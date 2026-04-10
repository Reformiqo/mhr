frappe.ui.form.on('Sales Order', {
    refresh: function(frm) {
        // Setup autocomplete dropdown for Container No
        let field = frm.fields_dict.custom_container_no;
        if (field && field.$input && !field.$input.data('awesomplete')) {
            let awesomplete = new Awesomplete(field.$input[0], {
                minChars: 0,
                maxItems: 20,
                autoFirst: true,
            });
            field.$input.data('awesomplete', awesomplete);

            // Fetch suggestions on focus and input
            function fetch_suggestions() {
                frappe.call({
                    method: 'mhr.sales_order.get_container_numbers',
                    args: { txt: field.$input.val() || '' },
                    callback: function(r) {
                        if (!r.message) return;
                        awesomplete.list = r.message;
                        awesomplete.evaluate();
                    }
                });
            }

            field.$input.on('focus', fetch_suggestions);
            field.$input.on('input', fetch_suggestions);

            // On selecting from dropdown, set value and fetch container details
            awesomplete.input.addEventListener('awesomplete-selectcomplete', function() {
                let container_no = field.$input.val();
                frm._container_selected = true;
                frm.set_value('custom_container_no', container_no);
            });
        }

        // Toggle field visibility based on Fetch By
        toggle_fetch_fields(frm);
    },

    custom_container_no: function(frm) {
        let container_no = frm.doc.custom_container_no;
        if (!container_no) {
            frm.set_value('custom_lot_no', '');
            frm.set_value('custom_daniar', '');
            return;
        }

        // Only fetch details when selected from dropdown, not on every keystroke
        if (!frm._container_selected) return;
        frm._container_selected = false;

        frappe.call({
            method: 'mhr.sales_order.get_container_details',
            args: { container_no: container_no },
            callback: function(r) {
                if (!r.message || !r.message.length) {
                    frappe.msgprint(__('No submitted Container found for {0}', [container_no]));
                    return;
                }

                let results = r.message;

                if (results.length === 1) {
                    frm.set_value('custom_lot_no', results[0].lot_no);
                    frm.set_value('custom_daniar', results[0].item);
                } else {
                    // Multiple combinations - let user pick
                    let options = results.map(function(r) {
                        return r.lot_no + ' | ' + r.item;
                    });

                    frappe.prompt({
                        label: __('Select Lot No / Item'),
                        fieldname: 'selection',
                        fieldtype: 'Select',
                        options: options,
                        reqd: 1
                    }, function(values) {
                        let idx = options.indexOf(values.selection);
                        let selected = results[idx];
                        frm.set_value('custom_lot_no', selected.lot_no);
                        frm.set_value('custom_daniar', selected.item);
                    }, __('Multiple entries found'), __('Select'));
                }
            }
        });
    },

    custom_fetch_by: function(frm) {
        toggle_fetch_fields(frm);
        // Clear the fields for the other mode
        frm.set_value('custom_cone', 0);
        frm.set_value('custom_no_of_boxes', 0);
        frm.set_value('custom_quantity_weight', 0);
    },

    custom_no_of_boxes: function(frm) {
        if (frm.doc.custom_fetch_by !== 'Cone and Boxes') return;
        let cone = frm.doc.custom_cone || 0;
        let boxes = frm.doc.custom_no_of_boxes || 0;
        if (cone && boxes) {
            fetch_and_fill_batches(frm);
        }
    },

    custom_cone: function(frm) {
        if (frm.doc.custom_fetch_by !== 'Cone and Boxes') return;
        let cone = frm.doc.custom_cone || 0;
        let boxes = frm.doc.custom_no_of_boxes || 0;
        if (cone && boxes) {
            fetch_and_fill_batches(frm);
        }
    },

    custom_quantity_weight: function(frm) {
        if (frm.doc.custom_fetch_by !== 'Weight') return;
        if (frm.doc.custom_quantity_weight) {
            fetch_and_fill_batches(frm);
        }
    }
});

function toggle_fetch_fields(frm) {
    let fetch_by = frm.doc.custom_fetch_by;
    // Cone and Boxes mode: show cone + boxes, hide weight
    frm.toggle_display('custom_cone', fetch_by === 'Cone and Boxes');
    frm.toggle_display('custom_no_of_boxes', fetch_by === 'Cone and Boxes');
    // Weight mode: show weight, hide cone + boxes
    frm.toggle_display('custom_quantity_weight', fetch_by === 'Weight');
}

function fetch_and_fill_batches(frm) {
    let item_code = frm.doc.custom_daniar;
    let container_no = frm.doc.custom_container_no;
    let lot_no = frm.doc.custom_lot_no;
    let fetch_by = frm.doc.custom_fetch_by;
    let boxes = frm.doc.custom_no_of_boxes || 0;
    let cone = frm.doc.custom_cone || 0;
    let qty = frm.doc.custom_quantity_weight || 0;

    if (!item_code) return;

    frappe.call({
        method: 'mhr.sales_order.get_so_batches',
        args: {
            item_code: item_code,
            container_no: container_no,
            lot_no: lot_no,
            cone: (fetch_by === 'Cone and Boxes') ? cone : 0,
            qty: (fetch_by === 'Weight') ? qty : 0,
            boxes: (fetch_by === 'Cone and Boxes') ? boxes : 0
        },
        callback: function(r) {
            if (!r.message || !r.message.length) {
                frappe.msgprint(__('No batches found with available stock for the given filters.'));
                return;
            }

            frm.clear_table('items');

            let batches = r.message;
            let total_qty = 0;
            let total_cones = 0;

            batches.forEach(function(batch) {
                let row = frm.add_child('items');
                row.item_code = batch.item;
                row.item_name = batch.item_name;
                row.stock_uom = batch.stock_uom;
                row.uom = batch.stock_uom;
                row.qty = batch.allotted_qty;
                row.custom_batch_no = batch.name;
                row.custom_lot_number = batch.custom_lot_no;
                row.custom_container_number = batch.custom_container_no;
                row.custom_grade = batch.custom_grade;
                row.custom_cone = batch.allotted_cones || 0;
                total_qty += batch.allotted_qty;
                total_cones += (batch.allotted_cones || 0);
            });

            frm.refresh_field('items');

            if (fetch_by === 'Cone and Boxes') {
                if (batches.length < boxes) {
                    frappe.msgprint(
                        __('Only {0} batch(es) available. Requested: {1} boxes',
                        [batches.length, boxes])
                    );
                }
                if (total_cones < cone) {
                    frappe.msgprint(
                        __('Only {0} cones available across {1} batch(es). Requested: {2}',
                        [total_cones, batches.length, cone])
                    );
                }
            }

            if (fetch_by === 'Weight') {
                // Auto-fill boxes and cone from result
                frm.set_value('custom_no_of_boxes', batches.length);
                frm.set_value('custom_cone', total_cones);
                if (total_qty < qty) {
                    frappe.msgprint(
                        __('Only {0} weight available across {1} batch(es). Requested: {2}',
                        [total_qty, batches.length, qty])
                    );
                }
            }
        }
    });
}

frappe.ui.form.on('Sales Order Item', {
    custom_batch_no: function(frm, cdt, cdn) {
        let row = locals[cdt][cdn];
        if (!row.custom_batch_no) return;

        frappe.call({
            method: 'mhr.sales_order.get_item_batch',
            args: { batch: row.custom_batch_no },
            callback: function(r) {
                if (!r.message || r.message.error) return;
                let d = r.message;
                frappe.model.set_value(cdt, cdn, {
                    'item_code': d.item_code,
                    'custom_lot_number': d.lot_no,
                    'custom_container_number': d.container_no,
                    'custom_grade': d.grade,
                    'custom_cone': d.cone
                });
            }
        });
    }
});
