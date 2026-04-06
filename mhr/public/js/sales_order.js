frappe.ui.form.on('Sales Order', {
    custom_container_no: function(frm) {
        let container_no = frm.doc.custom_container_no;
        if (!container_no) {
            frm.set_value('custom_lot_no', '');
            frm.set_value('custom_daniar', '');
            return;
        }

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

    custom_no_of_boxes: function(frm) {
        fetch_and_fill_batches(frm);
    },

    custom_cone: function(frm) {
        fetch_and_fill_batches(frm);
    },

    custom_quantity_weight: function(frm) {
        fetch_and_fill_batches(frm);
    }
});

function fetch_and_fill_batches(frm) {
    let item_code = frm.doc.custom_daniar;
    let container_no = frm.doc.custom_container_no;
    let lot_no = frm.doc.custom_lot_no;
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
            cone: cone,
            qty: qty,
            boxes: boxes
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

            // Auto-fill the fields that were not used as input
            if (boxes) {
                // Allocated by boxes — fill cone and weight
                frm.set_value('custom_cone', total_cones);
                frm.set_value('custom_quantity_weight', total_qty);
            } else if (cone) {
                // Allocated by cone — fill boxes and weight
                frm.set_value('custom_no_of_boxes', batches.length);
                frm.set_value('custom_quantity_weight', total_qty);
            } else if (qty) {
                // Allocated by weight — fill boxes and cone
                frm.set_value('custom_no_of_boxes', batches.length);
                frm.set_value('custom_cone', total_cones);
            } else {
                frm.set_value('custom_no_of_boxes', batches.length);
            }

            frm.refresh_field('items');

            if (boxes && batches.length < boxes) {
                frappe.msgprint(
                    __('Only {0} batch(es) available. Requested: {1} boxes',
                    [batches.length, boxes])
                );
            }

            if (cone && total_cones < cone) {
                frappe.msgprint(
                    __('Only {0} cones available across {1} batch(es). Requested: {2}',
                    [total_cones, batches.length, cone])
                );
            }

            if (qty && total_qty < qty) {
                frappe.msgprint(
                    __('Only {0} weight available across {1} batch(es). Requested: {2}',
                    [total_qty, batches.length, qty])
                );
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
