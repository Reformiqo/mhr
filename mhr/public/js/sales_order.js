frappe.ui.form.on('Sales Order', {
    custom_quantity_weight: function(frm) {
        fetch_and_fill_batches(frm);
    }
});

function fetch_and_fill_batches(frm) {
    let item_code = frm.doc.custom_daniar;
    let container_no = frm.doc.custom_container_no;
    let lot_no = frm.doc.custom_lot_no;
    let qty = frm.doc.custom_quantity_weight || 0;

    if (!item_code) return;

    frappe.call({
        method: 'mhr.sales_order.get_so_batches',
        args: {
            item_code: item_code,
            container_no: container_no,
            lot_no: lot_no,
            qty: qty
        },
        callback: function(r) {
            if (!r.message || !r.message.length) {
                frappe.msgprint(__('No batches found with available stock for the given filters.'));
                return;
            }

            frm.clear_table('items');

            let batches = r.message;
            let total_qty = 0;

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
                total_qty += batch.allotted_qty;
            });

            frm.refresh_field('items');

            if (qty && total_qty < qty) {
                frappe.msgprint(
                    __('Only {0} available across {1} batch(es). Requested: {2}',
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
                    'custom_grade': d.grade
                });
            }
        });
    }
});


