// MI1-I90 — Sales Order HTY mode.
//
// Port of the Delivery Note HTY behaviour onto Sales Order. Registered via
// doctype_js in hooks.py; it is the only app JS on the doctype.
//
// The VFY side belongs to the "Sales Order Booking" Client Script, which
// gates every one of its handlers on VFY. This file is the mirror image:
// every handler returns immediately unless transaction_type === 'HTY'. The
// two therefore never act on the same document, and load order between them
// does not matter.
//
// HARD RULE, mirroring the Delivery Note work: VFY Sales Orders behave
// exactly as they did before this file existed, and no Delivery Note code is
// touched.
//
// Ported from:
//   Client Script "HTY & VFY"                        -> Select Batch popup
//   Client Script "MI1-I39 - Delivery Note HTY Mode" -> label swap, lot picker,
//                                                       naming series, filters
//   Client Script "MI1-I39 - Sales Order HTY Mode"   -> company-aware filters
//                                                       (that record is disabled
//                                                        by the MI1-I90 patch)
//   Client Script "Fetch Batches"                    -> count-driven bulk fetch
//   Client Script "Total"                            -> total cone

const SO_HTY_SERIES = 'HTY-SO-.YYYY.-';

// Fields that mirror Delivery Note's HTY visibility rules. The HTY tab is
// only rendered in HTY mode, but we keep DN's exact hide-list inside it so
// the two forms present the same spec set.
const SO_HTY_HIDE_IN_HTY = [
    'custom_fsc',
    'custom_merge_no',
    'custom_cross_section',
    'custom_lusture',
    'custom_glue',
    'custom_pulp',
];
const SO_HTY_HIDE_IN_VFY = ['custom_colour', 'custom_product', 'custom_type'];

function so_hty_is_hty(frm) {
    return (frm.doc.transaction_type || '') === 'HTY';
}

// Drop the "Label-" prefix for display only: "Glue-CENT" -> "CENT".
// Splits on the FIRST hyphen — mhr.sales_order_hty._strip_label_prefix does
// the same, so popup and stored header value never disagree.
function so_hty_strip_label_prefix(val) {
    if (val === null || val === undefined || val === '') return '-';
    const s = String(val);
    const idx = s.indexOf('-');
    return idx >= 0 ? s.substring(idx + 1) : s;
}

// ---------------------------------------------------------------------------
// mode toggle: visibility + custom buttons
// ---------------------------------------------------------------------------

function so_hty_apply_mode(frm) {
    const hty = so_hty_is_hty(frm);
    so_hty_apply_fetch_by_options(frm);

    for (const f of SO_HTY_HIDE_IN_HTY) {
        if (frm.fields_dict[f]) frm.set_df_property(f, 'hidden', hty ? 1 : 0);
    }
    for (const f of SO_HTY_HIDE_IN_VFY) {
        if (frm.fields_dict[f]) frm.set_df_property(f, 'hidden', hty ? 0 : 1);
    }

    // Gross Weight + Sr. No. only carry meaning in HTY.
    const grid = frm.fields_dict.items && frm.fields_dict.items.grid;
    if (grid) {
        ['custom_gross_weight', 'custom_sr_no'].forEach((f) => {
            const df = grid.get_docfield(f);
            if (df) {
                df.hidden = hty ? 0 : 1;
                df.in_list_view = hty ? 1 : 0;
            }
        });
        grid.refresh();
    }

    // MI1-I91 (Raj 2026-09-03): HTY reuses the VFY booking controls on the
    // main tab — Container No -> Lot popup -> Fetch By -> Cone & Pallet /
    // Weight -> mhr.sales_order.get_so_batches. Only *Boxes* is hidden here:
    // HTY counts Pallets, so custom_no_of_pallet takes its place. Which of
    // pallet / weight is visible depends on Fetch By and is decided in
    // so_hty_apply_fetch_by. In VFY these fields belong to the "Sales Order
    // Booking" Client Script and are not touched.
    if (hty) {
        if (frm.fields_dict.custom_no_of_boxes) frm.toggle_display('custom_no_of_boxes', false);
        so_hty_apply_fetch_by(frm);
    }

    so_hty_add_lot_picker_button(frm, hty);
    so_hty_apply_company_filters(frm, hty);
}

function so_hty_add_lot_picker_button(frm, hty) {
    frm.remove_custom_button(__('Pick Containers by Lot'), __('HTY'));
    if (!hty || frm.doc.docstatus !== 0) return;
    frm.add_custom_button(
        __('Pick Containers by Lot'),
        () => so_hty_open_lot_picker(frm),
        __('HTY')
    );
}

// Ported from the Desk Client Script "MI1-I39 — Sales Order HTY Mode", which
// this file supersedes (the patch disables that record). FRD §SO rules 2-5:
// warehouse pickers filter by the selected Company, and Price List + Cost
// Center auto-fetch from the Company master on change. HTY only — VFY keeps
// stock ERPNext behaviour.
function so_hty_apply_company_filters(frm, hty) {
    if (!hty) return;
    frm.set_query('set_warehouse', () => ({
        filters: { company: frm.doc.company || '' },
    }));
    frm.set_query('warehouse', 'items', () => ({
        filters: { company: frm.doc.company || '' },
    }));
}

function so_hty_fetch_company_defaults(frm) {
    if (!so_hty_is_hty(frm) || !frm.doc.company) return;
    // MI1-I90 P2: Company has NO default_price_list field — ERPNext does not
    // scope Price Lists by Company. The old Client Script queried it anyway
    // and every HTY Sales Order threw
    // "Field not permitted in query: default_price_list".
    // Resolved server-side now, through ERPNext's real chain.
    frappe.call({
        method: 'mhr.sales_order_hty.get_company_hty_defaults',
        args: { company: frm.doc.company, customer: frm.doc.customer || null },
        callback: function (r) {
            const v = r.message;
            if (!v) return;
            if (v.selling_price_list && !frm.doc.selling_price_list) {
                frm.set_value('selling_price_list', v.selling_price_list);
            }
            if (v.cost_center && !frm.doc.cost_center) {
                frm.set_value('cost_center', v.cost_center);
            }
        },
    });
}

// ---------------------------------------------------------------------------
// naming series
// ---------------------------------------------------------------------------

// Fires only on a genuine transaction_type change — never on refresh. MI1-I74
// showed that a refresh-bound version stomps a manual pick every time the
// batch picker rewrites a header field.
//
// Unlike the Delivery Note version this remembers the series the document had
// before the switch and restores it, instead of falling back to options[0].
// On DN that fallback sends every document to MAT-DN-FY regardless of company
// or return status.
function so_hty_apply_naming_series(frm) {
    if (frm.doc.docstatus !== 0) return;
    const ns_field = frm.fields_dict.naming_series;
    const ns_df = ns_field && ns_field.df;
    if (!ns_df || !ns_df.options) return;

    const opts = String(ns_df.options).split('\n').filter(Boolean);
    const current = String(frm.doc.naming_series || '');

    if (so_hty_is_hty(frm)) {
        if (current.startsWith('HTY-')) return;
        const target = opts.indexOf(SO_HTY_SERIES) >= 0
            ? SO_HTY_SERIES
            : opts.find((o) => o.startsWith('HTY-'));
        if (!target) return;
        frm.__so_hty_prev_series = current;   // restore this on the way back
        frm.set_value('naming_series', target);
        return;
    }

    if (!current.startsWith('HTY-')) return;
    const restore = frm.__so_hty_prev_series
        || opts.find((o) => !o.startsWith('HTY-'));
    if (restore) frm.set_value('naming_series', restore);
}

// ---------------------------------------------------------------------------
// batch dropdown filters (MI1-I76 + MI1-I85 parity)
// ---------------------------------------------------------------------------

// HTY documents only see HTY batches, and never a batch with 0 cones — those
// land as zero-qty rows that cannot be fulfilled. Both filters must live in
// one handler because this is the last set_query registered for the field.
function so_hty_apply_batch_filters(frm) {
    if (!so_hty_is_hty(frm)) return;
    const filters = { custom_cone: ['>', 0], custom_transaction_type: 'HTY' };
    if (frm.fields_dict.custom_batch) {
        frm.set_query('custom_batch', () => ({ filters }));
    }
    frm.set_query('custom_batch_no', 'items', () => ({ filters }));
}

// ---------------------------------------------------------------------------
// totals
// ---------------------------------------------------------------------------

// Total Quantity is ERPNext's own field, normally filled by
// calculate_taxes_and_totals off the grid's items_add. Every HTY path below
// appends with frm.add_child, which fires no grid event, so nothing ran and
// Total Quantity stayed 0 — same fault as the Delivery Note's.
//
// The submitted-document guard is the MI1-I106 lesson: recomputing a total on
// an already-submitted form is what made a saved Delivery Note read
// "Not Saved". The server settled these at submit; leave them alone.
function so_hty_calculate_totals(frm) {
    if (!so_hty_is_hty(frm)) return;
    if (frm.doc.docstatus !== 0) return;

    let total_cone = 0;
    let total_qty = 0;
    (frm.doc.items || []).forEach((row) => {
        total_cone += parseInt(row.custom_cone || 0, 10) || 0;
        total_qty += parseFloat(row.qty || 0);
    });

    if (parseInt(frm.doc.custom_total_cone || 0, 10) !== total_cone) {
        frm.set_value('custom_total_cone', total_cone);
    }
    so_hty_set_if_changed(frm, 'total_qty', total_qty);
}

// frm.set_value dirties the form for any difference at all, a float rounding
// artefact included, so compare at the precision the field is stored with.
function so_hty_set_if_changed(frm, fieldname, value) {
    // Resolving precision must never throw: one exception here would leave the
    // total sitting at 0, which is the very thing this is here to prevent.
    let precision = null;
    try {
        precision = frm.precision(fieldname);
    } catch (e) {
        precision = null;
    }
    const next = flt(value, precision);
    if (flt(frm.doc[fieldname], precision) === next) return;
    frm.set_value(fieldname, next);
    frm.refresh_field(fieldname);
}

// Sort item rows by Supplier Batch No ascending and renumber idx so the saved
// order matches what the user sees (MI1-I75).
function so_hty_sort_items_by_supplier_batch(frm) {
    (frm.doc.items || []).sort((a, b) =>
        String(a.custom_supplier_batch_no || '').localeCompare(
            String(b.custom_supplier_batch_no || ''),
            undefined,
            { numeric: true, sensitivity: 'base' }
        )
    );
    (frm.doc.items || []).forEach((row, i) => {
        row.idx = i + 1;
    });
    frm.refresh_field('items');
}

// ---------------------------------------------------------------------------
// 4-step lot picker  (FRD: lot -> containers -> multi-select -> proceed)
// ---------------------------------------------------------------------------

function so_hty_open_lot_picker(frm) {
    const d = new frappe.ui.Dialog({
        title: __('HTY — Pick Containers by Lot'),
        size: 'large',
        fields: [
            {
                fieldname: 'step1_section',
                fieldtype: 'Section Break',
                label: __('Step 1 — Select Lot No'),
            },
            {
                fieldname: 'lot_no',
                fieldtype: 'Autocomplete',
                label: __('Lot No'),
                reqd: 1,
                description: __('Containers appear once a lot is picked.'),
            },
            {
                fieldname: 'step2_section',
                fieldtype: 'Section Break',
                label: __('Step 2 / 3 — Containers (multi-select)'),
            },
            {
                fieldname: 'containers_html',
                fieldtype: 'HTML',
                options:
                    '<p class="text-muted">' +
                    __('Select a Lot No above to load containers.') +
                    '</p>',
            },
        ],
        primary_action_label: __('Proceed'),
        primary_action: () => so_hty_lot_picker_proceed(frm, d),
    });

    frappe.call({
        method: 'mhr.utilis.get_hty_lots',
        args: { company: frm.doc.company || null },
        callback: function (r) {
            const lots = (r.message || []).map((row) => ({
                value: row.lot_no,
                label: row.lot_no + ' — ' + (row.container_count || 0) + ' container(s)',
            }));
            d.fields_dict.lot_no.df.options = lots;
            d.fields_dict.lot_no.refresh();
        },
    });

    d.fields_dict.lot_no.df.onchange = function () {
        const lot_no = d.get_value('lot_no');
        if (!lot_no) return;
        frappe.call({
            method: 'mhr.utilis.get_hty_containers_for_lot',
            args: { lot_no: lot_no, company: frm.doc.company || null },
            callback: (r) => so_hty_render_containers(d, r.message || []),
        });
    };

    d.show();
    d._hty_state = { containers: [] };
}

function so_hty_render_containers(d, containers) {
    d._hty_state.containers = containers;
    if (!containers.length) {
        d.fields_dict.containers_html.$wrapper.html(
            '<div class="text-muted">' +
                __('No submitted Containers for this lot.') +
                '</div>'
        );
        return;
    }

    const esc = frappe.utils.escape_html;
    let html = '<div style="max-height:360px;overflow:auto;">';
    html += '<table class="table table-bordered" style="font-size:12px;"><thead><tr>';
    html += '<th style="width:30px;"><input type="checkbox" class="hty-pick-all"/></th>';
    html += '<th>' + __('Container') + '</th>';
    html += '<th>' + __('Container No') + '</th>';
    html += '<th>' + __('Item') + '</th>';
    html += '<th class="text-right">' + __('Batches') + '</th>';
    html += '<th class="text-right">' + __('Total Cone') + '</th>';
    html += '<th class="text-right">' + __('Net Weight') + '</th>';
    html += '<th>' + __('Date') + '</th>';
    html += '</tr></thead><tbody>';
    containers.forEach((c) => {
        html += '<tr>';
        // data-container is read back with .attr(), not .data(): jQuery's
        // .data() coerces all-numeric container names to Number, and these
        // names are frequently all digits.
        html += `<td><input type="checkbox" class="hty-pick" data-container="${esc(c.container)}"/></td>`;
        html += `<td>${esc(c.container)}</td>`;
        html += `<td>${esc(c.container_no || '')}</td>`;
        html += `<td>${esc(c.item_code || '')}</td>`;
        html += `<td class="text-right">${esc(String(c.total_batches || 0))}</td>`;
        html += `<td class="text-right">${esc(String(c.total_cone || 0))}</td>`;
        html += `<td class="text-right">${esc(String(c.total_net_weight || 0))}</td>`;
        html += `<td>${esc(String(c.posting_date || ''))}</td>`;
        html += '</tr>';
    });
    html += '</tbody></table></div>';
    d.fields_dict.containers_html.$wrapper.html(html);

    d.fields_dict.containers_html.$wrapper.find('.hty-pick-all').on('change', function () {
        const checked = this.checked;
        d.fields_dict.containers_html.$wrapper.find('.hty-pick').prop('checked', checked);
    });
}

function so_hty_lot_picker_proceed(frm, d) {
    const $picks = d.fields_dict.containers_html.$wrapper.find('.hty-pick:checked');
    if (!$picks.length) {
        frappe.msgprint({
            title: __('Select at least one Container'),
            message: __('Step 3 requires multi-selecting Containers before Proceed.'),
            indicator: 'orange',
        });
        return;
    }
    const names = $picks.map((_, el) => $(el).attr('data-container')).get();

    frappe.call({
        method: 'mhr.sales_order_hty.get_so_rows_for_containers',
        args: { container_names: names },
        freeze: true,
        freeze_message: __('Loading batches…'),
        callback: function (r) {
            const rows = r.message || [];
            if (!rows.length) {
                frappe.msgprint(__('Selected Containers have no batch rows.'));
                return;
            }

            // Append rather than clear: the Select Batch popup may already have
            // put rows in. On Delivery Note the lot picker calls clear_table()
            // and silently discards them.
            const existing = new Set(
                (frm.doc.items || []).map((row) => row.custom_batch_no).filter(Boolean)
            );
            let added = 0;
            let skipped = 0;
            rows.forEach((row) => {
                if (row.custom_batch_no && existing.has(row.custom_batch_no)) {
                    skipped += 1;
                    return;
                }
                frm.add_child('items', row);
                if (row.custom_batch_no) existing.add(row.custom_batch_no);
                added += 1;
            });

            so_hty_sort_items_by_supplier_batch(frm);
            so_hty_calculate_totals(frm);
            d.hide();
            frappe.show_alert({
                message: __('Added {0} batch row(s) from {1} container(s). {2} already present.', [
                    added,
                    names.length,
                    skipped,
                ]),
                indicator: 'green',
            });
        },
    });
}

// ---------------------------------------------------------------------------
// "Select Batch" popup (container_no / denier triggers)
// ---------------------------------------------------------------------------

function so_hty_show_batch_dialog(frm, batches) {
    const batch_map = {};
    batches.forEach((b) => {
        batch_map[b.name] = b;
    });

    // Supplier Batch No ascending, numeric-aware so 4486 < 4487 < 7004 and
    // '10' does not sort before '9'. Blank sinks to the bottom.
    batches.sort((a, b) => {
        const sa = a.custom_supplier_batch_no || '';
        const sb = b.custom_supplier_batch_no || '';
        if (!sa && !sb) return 0;
        if (!sa) return 1;
        if (!sb) return -1;
        return String(sa).localeCompare(String(sb), undefined, {
            numeric: true,
            sensitivity: 'base',
        });
    });

    const esc = frappe.utils.escape_html;
    const rows_html = batches
        .map(
            (b) => `
        <tr data-batch="${esc(b.name)}">
            <td style="text-align:center;">
                <input type="checkbox" name="so_hty_batch" value="${esc(b.name)}">
            </td>
            <td>${esc(String(b.custom_lot_no || '-'))}</td>
            <td>${esc(String(b.custom_cone || '-'))}</td>
            <td>${esc(so_hty_strip_label_prefix(b.custom_glue))}</td>
            <td>${esc(so_hty_strip_label_prefix(b.custom_pulp))}</td>
            <td>${esc(so_hty_strip_label_prefix(b.custom_lusture))}</td>
            <td>${esc(so_hty_strip_label_prefix(b.custom_grade))}</td>
            <td>${esc(String(b.item || '-'))}</td>
            <td>${esc(String(b.manufacturing_date || '-'))}</td>
            <td>${esc(String(b.batch_qty || '-'))}</td>
            <td>${esc(String(b.stock_uom || '-'))}</td>
            <td>${esc(String(b.custom_supplier_batch_no || '-'))}</td>
            <td>${esc(String(b.custom_container_no || '-'))}</td>
            <td>${esc(String(b.custom_warehouse || '-'))}</td>
        </tr>`
        )
        .join('');

    const col_count = 14;
    const filter_cells =
        '<th></th>' +
        Array.from(
            { length: col_count - 1 },
            (_, i) =>
                `<th><input type="text" data-col="${i + 1}" placeholder="Filter…"
                    style="width:100%;box-sizing:border-box;font-size:11px;padding:2px 4px;border:1px solid #ccc;border-radius:3px;"></th>`
        ).join('');

    const dialog_content = `
        <div style="max-height:400px; overflow:auto;">
            <table class="table table-bordered table-striped" style="width:100%;margin-bottom:0;font-size:12px;">
                <thead>
                    <tr>
                        <th style="width:50px;text-align:center;">Select</th>
                        <th>Lot No</th><th>Cone</th><th>Product</th>
                        <th>Type</th><th>Colour</th><th>Grade</th>
                        <th>Item</th><th>Mfg Date</th><th>Batch Qty</th>
                        <th>Stock UOM</th><th>Supplier Batch No</th><th>Container No</th>
                        <th>Warehouse</th>
                    </tr>
                    <tr id="so_hty_filter_row">${filter_cells}</tr>
                </thead>
                <tbody id="so_hty_tbody">${rows_html}</tbody>
            </table>
        </div>`;

    const d = new frappe.ui.Dialog({
        title: __('Select Batch'),
        fields: [{ fieldtype: 'HTML', fieldname: 'batch_list', options: dialog_content }],
        size: 'extra-large',
        primary_action_label: __('Select'),
        primary_action: function () {
            const checked = d.wrapper.find('input[name="so_hty_batch"]:checked');
            if (!checked.length) {
                frappe.msgprint(__('Please select at least one batch'));
                return;
            }
            const selected = checked.map((_, el) => $(el).val()).get();
            so_hty_apply_selected_batches(frm, batch_map, selected, d);
        },
    });

    d.show();

    d.wrapper.on('input', '#so_hty_filter_row input', function () {
        const filters = [];
        d.wrapper.find('#so_hty_filter_row input').each(function () {
            const val = $(this).val().trim().toLowerCase();
            if (val) filters.push({ col: parseInt($(this).data('col'), 10), val });
        });
        d.wrapper.find('#so_hty_tbody tr').each(function () {
            const row = $(this);
            const visible = filters.every((f) =>
                row.find('td').eq(f.col).text().toLowerCase().includes(f.val)
            );
            row.toggle(visible);
        });
    });
}

function so_hty_apply_selected_batches(frm, batch_map, selected_names, d) {
    const last = batch_map[selected_names[selected_names.length - 1]];

    // Header spec from the last picked row, mirroring the Delivery Note popup.
    frm.set_value('custom_glue', last.custom_glue || '');
    frm.set_value('custom_pulp', last.custom_pulp || '');
    frm.set_value('custom_lusture', last.custom_lusture || '');
    frm.set_value('custom_grade', last.custom_grade || '');
    frm.set_value('custom_lot_no', last.custom_lot_no || '');
    frm.set_value('custom_fsc', last.custom_fsc || '');
    frm.set_value('custom_cone', last.custom_cone || 0);
    // Direct write: set_value here would re-fire the custom_denier handler
    // and stack a second popup on top of this one (MI1-I72 P3).
    frm.doc.custom_denier = last.item || '';
    frm.refresh_field('custom_denier');

    // Product / Type / Colour come from the Container. Resolved server-side
    // through the batch's Batch Items row, so we get the one Container the
    // batch actually belongs to — a container_no lookup is ambiguous, one
    // number maps to many Container docs that disagree with each other.
    frappe.call({
        method: 'mhr.sales_order_hty.get_container_spec_for_batch',
        args: { batch_no: last.name },
        callback: function (r) {
            const spec = r.message || {};
            frm.set_value('custom_product', spec.product || '');
            frm.set_value('custom_type', spec.type || '');
            frm.set_value('custom_colour', spec.colour || '');
            if (spec.merge_no) frm.set_value('custom_merge_no', spec.merge_no);
            if (spec.cross_section) frm.set_value('custom_cross_section', spec.cross_section);
            if (spec.notes && !frm.doc.custom_notes) frm.set_value('custom_notes', spec.notes);
        },
    });

    const existing = new Set(
        (frm.doc.items || []).map((row) => row.custom_batch_no).filter(Boolean)
    );
    let added = 0;
    let skipped = 0;

    selected_names.forEach((batch_name) => {
        const data = batch_map[batch_name];
        if (!data) return;
        if (existing.has(data.name)) {
            skipped += 1;
            return;
        }
        // batch_qty here is the clamped Serial-and-Batch-Bundle balance, so
        // <= 0 means there is nothing left to sell (MI1-I71 / MI1-I85).
        if (!(Number(data.batch_qty) > 0)) {
            skipped += 1;
            return;
        }
        frm.add_child('items', {
            item_code: data.item,
            item_name: data.item_name,
            qty: data.batch_qty,
            uom: data.stock_uom,
            stock_uom: data.stock_uom,
            warehouse: data.warehouse || frm.doc.set_warehouse,
            custom_batch_no: data.name,
            custom_cone: data.custom_cone,
            // The cone the row's qty was derived from. so_hty_recalc_row_qty
            // divides by it, so seeding it here is what makes a later cone
            // edit scale the qty instead of replacing it with the batch's
            // full quantity.
            custom_cone_copy: data.custom_cone,
            custom_supplier_batch_no: data.custom_supplier_batch_no,
            custom_container_number: data.custom_container_no,
            custom_lot_number: data.custom_lot_no,
        });
        existing.add(data.name);
        added += 1;
    });

    so_hty_sort_items_by_supplier_batch(frm);
    frm.set_value('custom_supplier_batch_no', '');
    so_hty_calculate_totals(frm);
    d.hide();

    if (skipped) {
        frappe.show_alert({
            message: __('Added {0} batch(es). {1} skipped (already present or no stock).', [
                added,
                skipped,
            ]),
            indicator: 'orange',
        });
    }
}

function so_hty_clear_spec_fields(frm) {
    [
        'custom_glue',
        'custom_pulp',
        'custom_lusture',
        'custom_grade',
        'custom_fsc',
        'custom_denier',
        'custom_cone',
        'custom_product',
        'custom_type',
        'custom_colour',
    ].forEach((f) => frm.set_value(f, ''));
}

// ---------------------------------------------------------------------------
// bulk fetch by count (Delivery Note "Fetch Batches" parity)
// ---------------------------------------------------------------------------

function so_hty_fetch_batches(frm) {
    frappe.call({
        method: 'mhr.note.fetch_batches',
        args: {
            limit: frm.doc.custom_count,
            lot_no: frm.doc.custom_lot_no,
            container_no: frm.doc.custom_container_no,
            glue: frm.doc.custom_glue,
            pulp: frm.doc.custom_pulp,
            fsc: frm.doc.custom_fsc,
            lusture: frm.doc.custom_lusture,
            grade: frm.doc.custom_grade,
            cone: frm.doc.custom_cone,
            denier: frm.doc.custom_denier,
        },
        freeze: true,
        freeze_message: __('Fetching batches…'),
        callback: function (r) {
            const batches = r.message || [];
            if (!batches.length) {
                frappe.msgprint(__('No batches found matching the criteria.'));
                return;
            }
            const existing = new Set(
                (frm.doc.items || []).map((row) => row.custom_batch_no).filter(Boolean)
            );
            let added = 0;
            let skipped = 0;
            batches.forEach((data) => {
                if (existing.has(data.name) || !(Number(data.batch_qty) > 0)) {
                    skipped += 1;
                    return;
                }
                frm.add_child('items', {
                    item_code: data.item,
                    item_name: data.item_name,
                    qty: data.batch_qty,
                    uom: data.stock_uom,
                    stock_uom: data.stock_uom,
                    custom_batch_no: data.name,
                    custom_cone: data.custom_cone,
                    custom_supplier_batch_no: data.custom_supplier_batch_no,
                    custom_container_number: data.custom_container_no,
                    custom_lot_number: data.custom_lot_no,
                });
                existing.add(data.name);
                added += 1;
                if (data.custom_notes && !frm.doc.custom_notes) {
                    frm.set_value('custom_notes', data.custom_notes);
                }
            });
            so_hty_sort_items_by_supplier_batch(frm);
            so_hty_calculate_totals(frm);
            frappe.show_alert({
                message: __('Added {0} batch(es), skipped {1}.', [added, skipped]),
                indicator: added ? 'green' : 'orange',
            });
        },
    });
}

// ---------------------------------------------------------------------------
// MI1-I91 (Raj 2026-09-03): the VFY booking flow on HTY
//   Container -> Lot popup -> Fetch By (Cone & Pallet | Weight)
//             -> mhr.sales_order.get_so_batches -> items
//
// Server logic is the VFY one, unchanged (get_container_details and
// get_so_batches gained optional args only). What differs is presentation:
// Boxes -> Pallet, lots filtered to available stock > 0, and Lot No + Denier
// filled on pick. The "Sales Order Booking" Client Script (VFY) is untouched.
// ---------------------------------------------------------------------------

const SO_HTY_FETCH_BY_OPTIONS = ['', 'Cone & Pallet', 'Weight'];
const SO_VFY_FETCH_BY_OPTIONS = ['', 'Cone and Boxes', 'Weight'];

// The DocField's stored options are the union of both modes: frappe validates
// a Select against its options on save, so "Cone & Pallet" must exist in the
// field for an HTY order to save at all. This narrows the *visible* list per
// mode. It is the one call in this file that also runs on a VFY document —
// it only trims a dropdown and never writes a value; without it a VFY user
// would be offered the HTY-only option.
function so_hty_apply_fetch_by_options(frm) {
    if (!frm.fields_dict.custom_fetch_by) return;
    const opts = so_hty_is_hty(frm) ? SO_HTY_FETCH_BY_OPTIONS : SO_VFY_FETCH_BY_OPTIONS;
    frm.set_df_property('custom_fetch_by', 'options', opts.join('\n'));
}

// Cone is always visible in HTY — it is part of the spec set the Select
// Batch popup fills (MI1-I90) and Raj's Cone & Pallet mode reads it. It
// has to be shown EXPLICITLY: a new Sales Order starts with an empty
// transaction_type, which the VFY "Sales Order Booking" Client Script
// treats as VFY, so its refresh hides custom_cone before the user picks
// HTY; once the doc is HTY that script stops acting and nothing else would
// restore the field (2026-09-03). Pallet and Weight follow Fetch By.
function so_hty_apply_fetch_by(frm) {
    if (!so_hty_is_hty(frm)) return;
    const mode = frm.doc.custom_fetch_by || '';
    if (frm.fields_dict.custom_cone) frm.toggle_display('custom_cone', true);
    if (frm.fields_dict.custom_no_of_pallet) {
        frm.toggle_display('custom_no_of_pallet', mode === 'Cone & Pallet');
    }
    if (frm.fields_dict.custom_quantity_weight) {
        frm.toggle_display('custom_quantity_weight', mode === 'Weight');
    }
}

function so_hty_open_lot_popup(frm) {
    const container_no = frm.doc.custom_container_no;
    frappe.call({
        method: 'mhr.sales_order.get_container_details',
        args: { container_no, transaction_type: 'HTY', with_stock: 1 },
        callback: function (r) {
            const rows = r.message || [];
            // Silent on empty: the user is probably still typing (MI1-I71).
            if (!rows.length) return;
            if (rows.length === 1) {
                so_hty_apply_lot_pick(frm, rows[0]);
                return;
            }
            const esc = frappe.utils.escape_html;
            let html =
                '<div style="max-height:400px;overflow-y:auto;">' +
                '<table class="table table-bordered table-striped" style="width:100%;font-size:12px;">' +
                '<thead><tr><th style="width:50px;text-align:center;">Select</th>' +
                '<th>Lot No</th><th>Item</th></tr></thead><tbody>';
            rows.forEach((row, i) => {
                html +=
                    `<tr><td style="text-align:center;"><input type="radio" name="so_hty_lot" value="${i}"></td>` +
                    `<td>${esc(String(row.lot_no || '-'))}</td>` +
                    `<td>${esc(String(row.item || '-'))}</td></tr>`;
            });
            html += '</tbody></table></div>';
            const d = new frappe.ui.Dialog({
                title: __('Select Lot'),
                fields: [{ fieldtype: 'HTML', fieldname: 'lot_list', options: html }],
                primary_action_label: __('Select'),
                primary_action: function () {
                    const $picked = d.wrapper.find('input[name="so_hty_lot"]:checked');
                    if (!$picked.length) {
                        frappe.msgprint(__('Please select a lot'));
                        return;
                    }
                    so_hty_apply_lot_pick(frm, rows[parseInt($picked.val(), 10)]);
                    d.hide();
                },
            });
            d.show();
        },
    });
}

function so_hty_apply_lot_pick(frm, row) {
    frm.set_value('custom_lot_no', row.lot_no || '');
    // Denier is the Item. Direct write: set_value would re-fire the
    // custom_denier handler and open the batch popup on top (MI1-I72 P3).
    frm.doc.custom_denier = row.item || '';
    frm.refresh_field('custom_denier');
    // Keep the main-tab Daniar in step so the form reads as it does in VFY.
    if (frm.fields_dict.custom_daniar) frm.set_value('custom_daniar', row.item || '');
}

function so_hty_fetch_by_allocation(frm) {
    const item_code = frm.doc.custom_denier || frm.doc.custom_daniar;
    if (!frm.doc.custom_container_no || !frm.doc.custom_lot_no || !item_code) return;
    const mode = frm.doc.custom_fetch_by;
    const args = {
        item_code,
        container_no: frm.doc.custom_container_no,
        lot_no: frm.doc.custom_lot_no,
        transaction_type: 'HTY',
    };
    if (mode === 'Cone & Pallet') {
        args.cone = frm.doc.custom_cone || 0;
        args.pallets = frm.doc.custom_no_of_pallet || 0;
        if (!args.pallets) return;
    } else if (mode === 'Weight') {
        args.qty = frm.doc.custom_quantity_weight || 0;
        if (!args.qty) return;
    } else {
        return;
    }

    frappe.call({
        method: 'mhr.sales_order.get_so_batches',
        args,
        freeze: true,
        freeze_message: __('Fetching batches…'),
        callback: function (r) {
            const batches = r.message || [];
            if (!batches.length) {
                frappe.msgprint(__('No batches available for the given filters.'));
                return;
            }
            frm.clear_table('items');
            let total_qty = 0;
            batches.forEach((b) => {
                frm.add_child('items', {
                    item_code: b.item,
                    item_name: b.item_name || b.item,
                    qty: b.allotted_qty,
                    uom: b.stock_uom,
                    stock_uom: b.stock_uom,
                    conversion_factor: 1,
                    custom_batch_no: b.name,
                    custom_cone: b.allotted_cones || b.custom_cone || 0,
                    // Divisor for so_hty_recalc_row_qty (MI1-I90): without it
                    // the first cone edit replaces qty with the batch's full
                    // quantity.
                    custom_cone_copy: b.custom_cone,
                    custom_supplier_batch_no: b.custom_supplier_batch_no,
                    custom_container_number: b.custom_container_no,
                    custom_lot_number: b.custom_lot_no,
                    custom_grade: b.custom_grade,
                });
                total_qty += flt(b.allotted_qty);
            });
            so_hty_sort_items_by_supplier_batch(frm);
            frm.refresh_field('items');
            so_hty_calculate_totals(frm);
            if (mode === 'Weight' && total_qty < flt(frm.doc.custom_quantity_weight)) {
                frappe.show_alert({
                    message: __(
                        'Fetched {0} across {1} full batch(es). Requested weight {2}; raise the weight to include the next batch (partial batches are never fetched).',
                        [total_qty, batches.length, frm.doc.custom_quantity_weight]
                    ),
                    indicator: 'blue',
                });
            }
        },
    });
}

// ---------------------------------------------------------------------------
// form bindings
// ---------------------------------------------------------------------------

frappe.ui.form.on('Sales Order', {
    refresh: function (frm) {
        so_hty_apply_mode(frm);
        so_hty_apply_batch_filters(frm);
    },

    // Last line of defence in the browser, whichever path added the rows.
    validate: function (frm) {
        so_hty_calculate_totals(frm);
    },

    transaction_type: function (frm) {
        so_hty_apply_mode(frm);
        so_hty_apply_batch_filters(frm);
        so_hty_apply_naming_series(frm);
        so_hty_fetch_company_defaults(frm);
    },

    company: function (frm) {
        so_hty_apply_mode(frm);          // re-wire set_query with the new company
        so_hty_fetch_company_defaults(frm);
    },

    custom_container_no: function (frm) {
        // VFY handler lives in the "Sales Order Booking" Client Script.
        if (!so_hty_is_hty(frm)) return;
        if (!frm.doc.custom_container_no) {
            so_hty_clear_spec_fields(frm);
            frm.set_value('custom_lot_no', '');
            return;
        }
        // MI1-I91 (Raj 2026-09-03): entering a Container opens the LOT popup
        // (Lot No + Item, available stock > 0 only) — the same step the VFY
        // booking flow starts with. The batch-level Select Batch popup stays
        // reachable through the Denier field and Pick Containers by Lot.
        so_hty_open_lot_popup(frm);
    },

    custom_fetch_by: function (frm) {
        if (!so_hty_is_hty(frm)) return;
        so_hty_apply_fetch_by(frm);
    },

    custom_no_of_pallet: function (frm) {
        if (!so_hty_is_hty(frm)) return;
        if (frm.doc.custom_fetch_by !== 'Cone & Pallet') return;
        so_hty_fetch_by_allocation(frm);
    },

    custom_quantity_weight: function (frm) {
        if (!so_hty_is_hty(frm)) return;
        if (frm.doc.custom_fetch_by !== 'Weight') return;
        so_hty_fetch_by_allocation(frm);
    },

    custom_denier: async function (frm) {
        if (!so_hty_is_hty(frm)) return;
        if (!frm.doc.custom_denier) return;
        // The container handler's popup is authoritative — without this the
        // denier handler stacks a second, broader modal on top (MI1-I71).
        if (frm.doc.custom_container_no) return;

        let all = [];
        let page = 0;
        const page_size = 50;
        // eslint-disable-next-line no-constant-condition
        while (true) {
            const r = await frappe.call({
                method: 'mhr.note.get_hty_batches_by_item',
                args: {
                    item: frm.doc.custom_denier,
                    limit_start: page * page_size,
                    limit_page_length: page_size,
                },
            });
            const batches = r.message || [];
            if (!batches.length) break;
            all = all.concat(batches);
            if (batches.length < page_size) break;
            page += 1;
        }
        if (!all.length) return;
        so_hty_show_batch_dialog(frm, all);
    },

    custom_fetch_batches: function (frm) {
        if (!so_hty_is_hty(frm)) return;
        if (!frm.doc.custom_fetch_batches) return;
        frm.set_value('custom_fetch_batches', 0);   // momentary trigger, not state
        if (!frm.doc.custom_count) {
            frappe.msgprint(__('Set a Count before fetching batches.'));
            return;
        }
        so_hty_fetch_batches(frm);
    },

    custom_scan_batch_no: function (frm) {
        if (!so_hty_is_hty(frm)) return;
        const scanned = frm.doc.custom_scan_batch_no;
        if (!scanned) return;
        frm.set_value('custom_scan_batch_no', '');

        if ((frm.doc.items || []).some((row) => row.custom_batch_no === scanned)) {
            frappe.msgprint(__('Batch {0} is already in the table.', [scanned]));
            return;
        }
        frappe.call({
            method: 'mhr.sales_order.get_item_batch',
            args: { batch: scanned },
            callback: function (r) {
                const d = r.message;
                if (!d || d.error) {
                    frappe.msgprint(__('Batch {0} not found.', [scanned]));
                    return;
                }
                frm.add_child('items', {
                    item_code: d.item_code,
                    item_name: d.item_name,
                    qty: d.qty,
                    uom: d.uom,
                    stock_uom: d.uom,
                    custom_batch_no: d.batch_no,
                    custom_cone: d.cone,
                    custom_supplier_batch_no: d.supplier_batch_no,
                    custom_container_number: d.container_no,
                    custom_lot_number: d.lot_no,
                });
                so_hty_sort_items_by_supplier_batch(frm);
                so_hty_calculate_totals(frm);
            },
        });
    },
});

// ---------------------------------------------------------------------------
// cone -> qty, the Sales Order half of the Delivery Note's 'Cone Qty
// Calcuation' Client Script
// ---------------------------------------------------------------------------
//
// Same arithmetic as the Delivery Note:
//
//     qty = (Batch.batch_qty * custom_cone) / custom_cone_copy
//
// so dropping a row's cone from 6 to 3 halves the ordered quantity instead of
// leaving the user to work it out. custom_cone_copy is the cone the row was
// created with, seeded in so_hty_apply_selected_batches.
//
// Three differences from the Delivery Note original, all deliberate:
//
//   * HTY only. A VFY Sales Order's qty belongs to the "Sales Order Booking"
//     Client Script and is not touched here.
//   * Bound through frappe.ui.form.on rather than the grid's jQuery
//     handlers. Same two triggers (cone changed / qty typed), but no
//     dependence on the grid's DOM or on data('prev-cone') surviving a
//     re-render.
//   * No is_return branch — a Sales Order cannot be a return.
//
// custom_qty_manual_edit carries the same meaning as on the Delivery Note: a
// qty the user typed themselves, which nothing may recompute. Both fields use
// the Delivery Note Item fieldnames, so Create > Delivery Note carries them
// across and the two forms agree on what a row's qty means.

function so_hty_recalc_row_qty(frm, cdt, cdn) {
    if (!so_hty_is_hty(frm)) return;

    const row = locals[cdt] && locals[cdt][cdn];
    if (!row || !row.custom_batch_no) return;

    // The user typed this qty. Leave it alone.
    if (row.custom_qty_manual_edit) return;

    const cone = parseFloat(row.custom_cone);
    const cone_copy = parseFloat(row.custom_cone_copy);
    if (isNaN(cone) || isNaN(cone_copy) || cone_copy === 0) return;

    frappe.call({
        method: 'frappe.client.get_value',
        args: {
            doctype: 'Batch',
            filters: { name: row.custom_batch_no },
            fieldname: 'batch_qty',
        },
        callback: function (r) {
            // Re-check: the user may have typed a qty while this was in flight.
            if (row.custom_qty_manual_edit) return;
            if (!r || !r.message || !r.message.batch_qty) return;

            const batch_qty = parseFloat(r.message.batch_qty);
            if (isNaN(batch_qty)) return;

            frappe.model.set_value(cdt, cdn, 'qty', flt((batch_qty * cone) / cone_copy, 3));
        },
    });
}

frappe.ui.form.on('Sales Order Item', {
    items_add: so_hty_calculate_totals,
    items_remove: so_hty_calculate_totals,

    custom_cone: function (frm, cdt, cdn) {
        so_hty_calculate_totals(frm);
        if (!so_hty_is_hty(frm)) return;

        const row = locals[cdt] && locals[cdt][cdn];
        if (!row) return;

        // A cone edit is an instruction to re-derive the qty, so it clears the
        // manual-edit flag first — same order as the Delivery Note.
        if (row.custom_qty_manual_edit) {
            frappe.model.set_value(cdt, cdn, 'custom_qty_manual_edit', 0);
        }
        // A row that arrived without a cone_copy (older document, or a row
        // added by hand) has nothing to scale against. Anchor it to the cone
        // it has now, which makes this edit a no-op rather than a jump to the
        // batch's full quantity.
        if (!row.custom_cone_copy) {
            frappe.model.set_value(cdt, cdn, 'custom_cone_copy', row.custom_cone);
            return;
        }

        so_hty_recalc_row_qty(frm, cdt, cdn);
    },

    qty: function (frm, cdt, cdn) {
        if (!so_hty_is_hty(frm)) return;

        const row = locals[cdt] && locals[cdt][cdn];
        // Only a row under cone control can be "manually" overridden; without
        // a batch nothing would have recomputed it anyway.
        if (!row || !row.custom_batch_no) return;
        if (row.custom_qty_manual_edit) return;

        frappe.model.set_value(cdt, cdn, 'custom_qty_manual_edit', 1);
    },
});
