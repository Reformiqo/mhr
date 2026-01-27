frappe.ui.form.on("Container", {
    refresh: function(frm) {
        // Add buttons for submitted containers
        if (frm.doc.docstatus === 1) {
            // Resubmit button
            frm.add_custom_button(__('Resubmit'), function() {
                frappe.confirm(
                    __('This will delete existing batches and Purchase Receipt, then recreate them. Are you sure?'),
                    function() {
                        frappe.call({
                            method: 'resubmit_container',
                            doc: frm.doc,
                            freeze: true,
                            freeze_message: __('Resubmitting Container...'),
                            callback: function(r) {
                                if (r.message) {
                                    frappe.msgprint({
                                        title: __('Success'),
                                        message: r.message.message + (r.message.purchase_receipt ? '<br>Purchase Receipt: ' + r.message.purchase_receipt : ''),
                                        indicator: 'green'
                                    });
                                    frm.reload_doc();
                                }
                            }
                        });
                    }
                );
            }, __('Actions'));

            // Debug button
            frm.add_custom_button(__('Debug'), function() {
                frappe.call({
                    method: 'debug_container',
                    doc: frm.doc,
                    freeze: true,
                    freeze_message: __('Analyzing Container...'),
                    callback: function(r) {
                        if (r.message) {
                            show_debug_dialog(frm, r.message);
                        }
                    }
                });
            }, __('Actions'));
        }
    },
    qty: function(frm, cdt, cdn) {
        console.log("qty");
        var d = locals[cdt][cdn];
    }
});

function show_debug_dialog(frm, debug_info) {
    let html = build_debug_html(debug_info);

    let d = new frappe.ui.Dialog({
        title: __('Container Debug Analysis'),
        size: 'extra-large',
        fields: [
            {
                fieldtype: 'HTML',
                fieldname: 'debug_content'
            }
        ]
    });

    d.fields_dict.debug_content.$wrapper.html(html);
    d.show();
}

function build_debug_html(info) {
    let html = '<div class="container-debug" style="max-height: 70vh; overflow-y: auto;">';

    // Summary Section
    html += '<div class="section-head">Summary</div>';
    html += '<div class="row mb-4">';
    html += `<div class="col-md-6">
        <table class="table table-bordered table-sm">
            <tr><td><strong>Container</strong></td><td>${info.container.name}</td></tr>
            <tr><td><strong>Container No</strong></td><td>${info.container.container_no || '-'}</td></tr>
            <tr><td><strong>Lot No</strong></td><td>${info.container.lot_no || '-'}</td></tr>
            <tr><td><strong>Total Batches in Table</strong></td><td>${info.container.total_batches_in_table}</td></tr>
        </table>
    </div>`;

    html += `<div class="col-md-6">
        <table class="table table-bordered table-sm">
            <tr><td><strong>Batches Exist</strong></td><td class="text-success">${info.summary.batches_exist}</td></tr>
            <tr><td><strong>Batches Missing</strong></td><td class="${info.summary.batches_missing > 0 ? 'text-danger' : ''}">${info.summary.batches_missing}</td></tr>
            <tr><td><strong>Wrong Container</strong></td><td class="${info.summary.batches_with_wrong_container > 0 ? 'text-warning' : ''}">${info.summary.batches_with_wrong_container}</td></tr>
            <tr><td><strong>Mismatched DNs</strong></td><td class="${info.summary.mismatched_delivery_notes > 0 ? 'text-danger' : ''}">${info.summary.mismatched_delivery_notes}</td></tr>
        </table>
    </div>`;
    html += '</div>';

    // Issues Section
    if (info.issues && info.issues.length > 0) {
        html += '<div class="section-head text-danger">Issues Found (' + info.issues.length + ')</div>';
        html += '<div class="mb-4">';
        info.issues.forEach(function(issue) {
            let badge_class = issue.type === 'CRITICAL' ? 'badge-danger' :
                             (issue.type === 'ERROR' ? 'badge-danger' : 'badge-warning');
            html += `<div class="alert alert-${issue.type === 'CRITICAL' ? 'danger' : (issue.type === 'ERROR' ? 'danger' : 'warning')} p-2 mb-2">
                <span class="badge ${badge_class}">${issue.type}</span>
                ${issue.batch_id ? '<strong>[' + issue.batch_id + ']</strong> ' : ''}
                ${issue.message}
            </div>`;
        });
        html += '</div>';
    } else {
        html += '<div class="alert alert-success">No issues found!</div>';
    }

    // Purchase Receipts Section
    html += '<div class="section-head">Purchase Receipts</div>';
    if (info.purchase_receipts && info.purchase_receipts.length > 0) {
        html += '<table class="table table-bordered table-sm mb-4"><thead><tr><th>Name</th><th>Status</th><th>Date</th></tr></thead><tbody>';
        info.purchase_receipts.forEach(function(pr) {
            let status = pr.docstatus === 1 ? '<span class="text-success">Submitted</span>' :
                        (pr.docstatus === 2 ? '<span class="text-danger">Cancelled</span>' : '<span class="text-muted">Draft</span>');
            html += `<tr><td><a href="/app/purchase-receipt/${pr.name}" target="_blank">${pr.name}</a></td><td>${status}</td><td>${pr.posting_date || '-'}</td></tr>`;
        });
        html += '</tbody></table>';
    } else {
        html += '<div class="alert alert-warning mb-4">No Purchase Receipts found</div>';
    }

    // Batches Detail Section
    html += '<div class="section-head">Batch Details</div>';
    info.batches.forEach(function(batch, idx) {
        let status_badge = batch.exists ?
            '<span class="badge badge-success">Exists</span>' :
            '<span class="badge badge-danger">Missing</span>';

        let has_issues = batch.issues && batch.issues.length > 0;
        let card_class = has_issues ? 'border-warning' : '';

        html += `<div class="card mb-3 ${card_class}">
            <div class="card-header" style="padding: 8px 12px; cursor: pointer;" onclick="this.nextElementSibling.style.display = this.nextElementSibling.style.display === 'none' ? 'block' : 'none'">
                <strong>${batch.batch_id}</strong> ${status_badge}
                ${has_issues ? '<span class="badge badge-warning ml-2">' + batch.issues.length + ' issues</span>' : ''}
                <span class="float-right text-muted">${batch.item} | Qty: ${batch.qty}</span>
            </div>
            <div class="card-body" style="display: none; padding: 12px;">`;

        if (batch.batch_data) {
            html += `<table class="table table-sm table-bordered mb-3">
                <tr><td width="30%"><strong>Container No (in Batch)</strong></td><td>${batch.batch_data.custom_container_no || '-'}</td></tr>
                <tr><td><strong>Lot No (in Batch)</strong></td><td>${batch.batch_data.custom_lot_no || '-'}</td></tr>
                <tr><td><strong>Batch Qty</strong></td><td>${batch.batch_data.batch_qty || 0}</td></tr>
            </table>`;
        }

        // Serial and Batch Bundles
        if (batch.serial_batch_bundles && batch.serial_batch_bundles.length > 0) {
            html += '<div class="mb-2"><strong>Serial and Batch Bundles:</strong></div>';
            html += '<table class="table table-sm table-bordered"><thead><tr><th>Bundle</th><th>Type</th><th>Voucher</th><th>Status</th><th>Qty</th></tr></thead><tbody>';

            batch.serial_batch_bundles.forEach(function(bundle) {
                let voucher_link = bundle.voucher_no ?
                    `<a href="/app/${frappe.router.slug(bundle.voucher_type)}/${bundle.voucher_no}" target="_blank">${bundle.voucher_no}</a>` : '-';

                let type_badge = bundle.type_of_transaction === 'Inward' ?
                    '<span class="badge badge-info">Inward</span>' :
                    '<span class="badge badge-warning">Outward</span>';

                let status = bundle.is_cancelled ? '<span class="text-danger">Cancelled</span>' :
                            (bundle.docstatus === 1 ? '<span class="text-success">Submitted</span>' : '<span class="text-muted">Draft</span>');

                html += `<tr>
                    <td><a href="/app/serial-and-batch-bundle/${bundle.bundle_name}" target="_blank">${bundle.bundle_name.substring(0, 15)}...</a></td>
                    <td>${type_badge}</td>
                    <td>${bundle.voucher_type}: ${voucher_link}</td>
                    <td>${status}</td>
                    <td>${bundle.entry_qty}</td>
                </tr>`;

                // Show Delivery Note details if mismatched
                if (bundle.delivery_note_data) {
                    let dn = bundle.delivery_note_data;
                    let is_mismatch = dn.custom_container_no && dn.custom_container_no !== frm.doc.container_no;
                    if (is_mismatch) {
                        html += `<tr class="bg-light">
                            <td colspan="5" class="text-danger">
                                <strong>DN Container:</strong> ${dn.custom_container_no || '-'} |
                                <strong>DN Lot:</strong> ${dn.custom_lot_no || '-'} |
                                <strong>Customer:</strong> ${dn.customer_name || '-'}
                                <br><small class="text-danger">This batch belongs to a DIFFERENT container!</small>
                            </td>
                        </tr>`;
                    }
                }
            });
            html += '</tbody></table>';
        } else {
            html += '<div class="text-muted">No Serial and Batch Bundles found</div>';
        }

        html += '</div></div>';
    });

    html += '</div>';

    // Add some CSS
    html = `<style>
        .container-debug .section-head {
            font-weight: bold;
            font-size: 14px;
            margin-bottom: 10px;
            padding-bottom: 5px;
            border-bottom: 1px solid #ddd;
        }
        .container-debug .card-header {
            background-color: #f8f9fa;
        }
        .container-debug .badge {
            font-size: 11px;
        }
    </style>` + html;

    return html;
}

frappe.ui.form.on("Batches", {
    qty: function(frm, cdt, cdn) {
        console.log("Batches qty field changed.");
        var d = locals[cdt][cdn];
        console.log("Current row data:", d);
        var total = 0;
        
        // Ensure frm.doc.batches exists and is an array
        if (frm.doc.batches && Array.isArray(frm.doc.batches)) {
            frm.doc.batches.forEach(function(batch) {
                total += batch.qty || 0; // Add a default value of 0 to handle undefined
            });
        } else {
            console.log("frm.doc.batches is not defined or not an array");
        }

        console.log("Total quantity:", total);
        frm.set_value("total_batches", total);
    }
});
