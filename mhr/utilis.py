import frappe
from frappe import _
from frappe.utils import cint, flt
from frappe.utils.print_format import download_multi_pdf
import json


def hty_qr_data_url(text):
    """MI1-I62: render a QR code as a base64 PNG data URL for inline use
    in Jinja print formats. Uses `segno` (already in the bench env), so
    no remote service or pre-generated file is needed. Returns "" for
    falsy input. Exposed to Jinja via hooks.py `jinja.methods`."""
    if not text:
        return ""
    import io
    import base64
    import segno
    qr = segno.make(str(text), error="M")
    buf = io.BytesIO()
    qr.save(buf, kind="png", scale=4, border=1)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


# MI1-I62: per-label HTML used by both the single-batch print format
# (preview) and the 6-up A4 bulk PDF (Print Batch). Centralised here so
# the layout doesn't drift between the two paths.
HTY_LABEL_HTML = """
<div class="hty-label">
  <table class="outer"><tbody>
    <tr>
      <td class="fields-col">
        <table class="fields"><tbody>
          <tr class="b"><td class="k">Container No.</td><td class="v">{{ doc.custom_container_no or "" }}</td></tr>
          <tr><td class="k">Pallet No.</td><td class="v">{{ doc.custom_cone or "" }}</td></tr>
          <tr class="b"><td class="k">Den/Fil</td><td class="v">{{ item_code }}</td></tr>
          <tr><td class="k">Cone</td><td class="v">{{ cone_val }}</td></tr>
          <tr class="b"><td class="k">Net Wt</td><td class="v">{{ net_wt_str }}</td></tr>
          <tr><td class="k">Gross Wt</td><td class="v">{{ gross_wt_str }}</td></tr>
          <tr><td class="k">Grade</td><td class="v">{{ grade_val }}</td></tr>
          <tr><td class="k">Luster</td><td class="v">{{ luster_val }}</td></tr>
          <tr><td class="k">Type</td><td class="v">PALLET</td></tr>
          <tr><td class="k">Lot No.</td><td class="v">{{ doc.custom_lot_no or "" }}</td></tr>
        </tbody></table>
      </td>
      <td class="right-col">
        <div class="serial">{{ serial }}</div>
        <img class="qr" src="{{ qr_url }}" alt="QR" />
      </td>
    </tr>
  </tbody></table>
</div>
""".strip()


HTY_6UP_STYLE = """
<style>
  /* Absolute positioning — wkhtmltopdf cannot misinterpret fixed mm
     coordinates. Each `.page` is exactly A4 (210x297mm) with 6 cells
     hard-pinned at predetermined (top,left) offsets. Pages are broken
     via `page-break-before: always` on every .page except :first-of-type
     (canonical multi-page HTML pattern). Earlier table-based attempts
     left wkhtmltopdf room to decide row-3-doesn't-fit; this leaves no
     such room. */
  html, body { margin: 0; padding: 0; }
  body { font-family: Arial, Helvetica, sans-serif; color: #000; font-size: 8.5pt; }
  @page { size: A4 portrait; margin: 0; }

  .page {
    position: relative;
    width: 210mm; height: 280mm;          /* less than A4 (297mm) */
    overflow: hidden;
    page-break-inside: avoid;
  }
  /* Height: 280mm gives 17mm of slack vs the 297mm A4 page. Two effects:
     (1) Drift slack — wkhtmltopdf's per-block rounding can't push the
         bottom row off (the '6+6+4' pattern we hit at exact 297mm).
     (2) Natural-flow pagination — when the next .page (also 280mm) won't
         fit in the remaining 17mm of the current PDF page, wkhtmltopdf
         page-breaks on its own. We DELIBERATELY don't add
         page-break-after: always: combining the explicit break with the
         already-triggered natural break produced an extra blank page
         after every real one (the bug Raj saw on page 4 of 6). With just
         natural overflow + page-break-inside: avoid, the math gives
         exactly N PDF pages for N .page blocks, no blanks. */

  /* 3 rows x 2 cols. Cells 90mm tall fit comfortably inside 280mm
     (3 * 90 = 270mm + 10mm slack). Two 96mm columns + 2mm centre
     gutter at left positions 5mm and 107mm. */
  .cell {
    position: absolute;
    width: 96mm; height: 90mm;
    padding: 3mm 4mm;
    box-sizing: border-box;
    overflow: hidden;
  }
  .cell.r1 { top: 0mm; }
  .cell.r2 { top: 93mm; }
  .cell.r3 { top: 186mm; }
  .cell.c1 { left: 5mm; }
  .cell.c2 { left: 107mm; }

  .hty-label { padding: 0; box-sizing: border-box; }
  table.outer { width: 100%; height: 100%; border-collapse: collapse; }
  table.outer > tbody > tr > td { vertical-align: top; padding: 0; }
  table.outer > tbody > tr > td.fields-col { width: 65%; padding-right: 2mm; }
  /* Right column: serial top, QR centered below. Vertical-align middle
     so the QR doesn't crowd the top edge — matches Raj's reference. */
  table.outer > tbody > tr > td.right-col {
    width: 35%; text-align: right; vertical-align: top;
    padding-top: 3mm;
  }
  table.fields { width: 100%; border-collapse: collapse; }
  /* Spacing 2026-06-23 (second iter): the 2mm + line-height-1.4 from
     the first attempt pushed total content to ~85mm — right at the
     cell's 84mm content area (90mm cell - 6mm vertical padding).
     wkhtmltopdf's overflow:hidden on absolutely-positioned cells is
     unreliable when content equals/exceeds the box, so labels were
     spilling across page boundaries (Raj's screenshot: top half of
     QR on one page, bottom half on the next).
     Trimmed to 1.5mm padding + 1.3 line-height:
       per-row = 1.5*2 + 3.2pt*1.3 ≈ 7.2mm
       10 rows × 7.2mm = 72mm + 6mm cell padding = 78mm in a 90mm cell
       → 12mm headroom, no spillover. Still airier than the original
       0.3mm + line-height-1.1. */
  table.fields td { padding: 1.5mm 1mm; vertical-align: top; line-height: 1.3; }
  /* Non-bold labels: regular weight (reference shows only Container No.,
     Den/Fil, Net Wt as bold). The tr.b override below handles those. */
  table.fields td.k { font-weight: normal; width: 42%; white-space: nowrap; }
  table.fields td.v { width: 58%; }
  /* Bold rows: Container No., Den/Fil, Net Wt (label + value). */
  table.fields tr.b td { font-weight: bold; }
  /* Serial number sits above the QR, right-aligned, prominent. */
  .right-col .serial { font-weight: bold; font-size: 10pt; margin-bottom: 3mm; }
  /* QR sized to fit the right column without forcing the QR off the
     cell when the field column is at its tallest. 26mm gives ~6mm
     slack between the serial line and the bottom of the cell. */
  .right-col img.qr { width: 26mm; height: 26mm; }
</style>
""".strip()


def render_hty_6up_pdf(batch_names):
    """MI1-I62 (final): render N HTY Batch Labels into ONE A4 PDF — 6 labels
    per page, 2 cols × 3 rows, matching Raj's reference PDF.

    Returns the PDF bytes. Skips (and logs) any batch name that no longer
    exists in `tabBatch`. Pads the final page with empty cells so the
    grid stays clean when N is not a multiple of 6.
    """
    if not batch_names:
        return b""

    labels = []
    for name in batch_names:
        if not frappe.db.exists("Batch", name):
            frappe.log_error(f"HTY 6up render: Batch {name} not found", "HTY 6up render")
            continue
        doc = frappe.get_doc("Batch", name)
        qr_payload = "{}_{}_{}".format(
            doc.custom_cone or "",
            doc.custom_container_no or "",
            doc.custom_lot_no or "",
        )
        ctx = {
            "doc": doc,
            "item_code": doc.item or "",
            "cone_val": hty_parse_filament_count(doc.item or ""),
            "net_wt_str": ("%.3f" % float(doc.batch_qty)) if doc.batch_qty is not None else "",
            "gross_wt_str": ("%.3f" % float(doc.get("custom_gross_weight")))
                if doc.get("custom_gross_weight") else "",
            "grade_val": strip_prefix(doc.custom_grade),
            "luster_val": strip_prefix(doc.custom_lusture),
            "serial": doc.custom_supplier_batch_no or doc.name,
            "qr_payload": qr_payload,
            "qr_url": hty_qr_data_url(qr_payload),
        }
        labels.append(frappe.render_template(HTY_LABEL_HTML, ctx))

    if not labels:
        return b""

    # Pad to next multiple of 6 so the last sheet has 6 cells.
    while len(labels) % 6 != 0:
        labels.append("")

    # Six cells per page at absolute (row, col) coordinates. row indices
    # map to top: 5mm / 99mm / 193mm; col indices to left: 5mm / 107mm.
    # The order is left-to-right, top-to-bottom (reading order).
    positions = [
        ("r1", "c1"), ("r1", "c2"),
        ("r2", "c1"), ("r2", "c2"),
        ("r3", "c1"), ("r3", "c2"),
    ]
    pages = []
    for i in range(0, len(labels), 6):
        chunk = labels[i:i + 6]
        cells = "".join(
            f'<div class="cell {row} {col}">{chunk[k]}</div>'
            for k, (row, col) in enumerate(positions)
        )
        pages.append(f'<div class="page">{cells}</div>')

    # Each .page self-page-breaks via CSS, and `:last-of-type
    # { page-break-after: auto }` prevents a trailing blank page.
    html = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        + HTY_6UP_STYLE
        + "</head><body>"
        + "".join(pages)
        + "</body></html>"
    )

    from frappe.utils.pdf import get_pdf
    # Margins = 0 because the @page CSS rule and .page width/height are
    # set explicitly. Letting wkhtmltopdf add margins on top would push
    # row 3 off the page.
    return get_pdf(html, options={
        "page-size": "A4",
        "margin-top": "0",
        "margin-bottom": "0",
        "margin-left": "0",
        "margin-right": "0",
    })


@frappe.whitelist()
def make_receive_from_subcontractor(source_name):
    """MI1-I50 P2: build a draft Stock Entry that receives material back
    from a subcontractor against a submitted 'Send to Subcontractor' SE.

    - Source must be Submitted + purpose 'Send to Subcontractor'.
    - Per item, pending = qty - custom_received_qty. Skip fully-received.
    - New entry has warehouses REVERSED (s_warehouse <-> t_warehouse) so
      stock flows subcontractor -> internal.
    - All custom fields on items (cone, lot, container, supplier batch,
      gross weight, etc.) are carried over.
    - Links back to the original via custom_original_send_entry.

    Returns the new draft's name; caller (JS) navigates to it.
    """
    source = frappe.get_doc("Stock Entry", source_name)
    if source.docstatus != 1:
        frappe.throw(_("Source Stock Entry must be Submitted."))
    if source.purpose != "Send to Subcontractor":
        frappe.throw(_("Source Stock Entry purpose must be 'Send to Subcontractor'."))

    receipt = frappe.new_doc("Stock Entry")
    receipt.stock_entry_type = "Material Transfer"
    receipt.purpose = "Material Transfer"
    receipt.company = source.company
    receipt.posting_date = frappe.utils.nowdate()
    receipt.posting_time = frappe.utils.nowtime()
    receipt.set_posting_time = 1
    receipt.set("custom_original_send_entry", source.name)

    # Carry header custom fields if the doctype defines them.
    carry_header = (
        "custom_container_number", "custom_lot_no", "custom_glue",
        "custom_pulp", "custom_lusture", "custom_grade", "custom_fsc",
        "custom_notes", "custom_denier", "custom_merge_no",
    )
    for f in carry_header:
        v = source.get(f)
        if v is not None and source.meta.has_field(f):
            receipt.set(f, v)

    # Items — only those with pending qty > 0.
    item_custom_fields = (
        "custom_cone", "custom_lot_no", "custom_container_no",
        "custom_supplier_batch_no", "custom_gross_weight",
    )
    appended = 0
    for src_item in source.items:
        sent_qty = flt(src_item.qty)
        already = flt(src_item.get("custom_received_qty") or 0)
        pending = sent_qty - already
        if pending <= 0:
            continue
        row = {
            "item_code": src_item.item_code,
            "item_name": src_item.item_name,
            "qty": pending,
            "uom": src_item.uom,
            "stock_uom": src_item.stock_uom,
            "conversion_factor": src_item.conversion_factor or 1,
            "batch_no": src_item.batch_no,
            "serial_no": src_item.serial_no,
            "use_serial_batch_fields": src_item.get("use_serial_batch_fields") or 0,
            # REVERSE warehouses so stock flows subcontractor -> internal.
            "s_warehouse": src_item.t_warehouse,
            "t_warehouse": src_item.s_warehouse,
            "allow_zero_valuation_rate": src_item.get("allow_zero_valuation_rate") or 0,
            "basic_rate": src_item.basic_rate or 0,
        }
        for cf in item_custom_fields:
            v = src_item.get(cf)
            if v is not None:
                row[cf] = v
        receipt.append("items", row)
        appended += 1

    if not appended:
        frappe.throw(_("Nothing to receive — all items on this Send entry are already fully received."))

    receipt.insert(ignore_permissions=True)
    return {"name": receipt.name}


# ---------------------------------------------------------------------------
# MI1-I50 P3 — qty recompute hooks + over-receipt validation
# ---------------------------------------------------------------------------
# A "Receive entry" is any Stock Entry whose custom_original_send_entry points
# at a submitted Send-to-Subcontractor entry. The hooks below run on EVERY
# Stock Entry (cheap fast-path early return when custom_original_send_entry
# is empty) so we don't have to special-case the doctype.
#
# Status options on the source's custom_subcontract_status select:
#   "Open"                — no qty received yet
#   "Partially Received"  — some but not all
#   "Fully Received"      — total_received >= total_sent (within rounding)

_SUBCONTRACT_STATUS_OPEN = "Open"
_SUBCONTRACT_STATUS_PARTIAL = "Partially Received"
_SUBCONTRACT_STATUS_FULL = "Fully Received"
_SUBCONTRACT_QTY_EPSILON = 0.0001


def _subcontract_source_name(doc):
    """Return the source Send-entry name if this SE is a receipt against one,
    else None. Fast-path used by every hook."""
    return doc.get("custom_original_send_entry") or None


def _subcontract_match_key(item):
    """(item_code, batch_no) — the join key between Receive and Source rows.

    Source-built receipts mirror source rows 1:1, but the user can edit qty
    or add rows. We aggregate by this key on both sides so multi-row source
    + edited receipt still reconciles correctly."""
    return (item.item_code, (item.batch_no or ""))


@frappe.whitelist()
def validate_subcontract_receipt(doc, method=None):
    """MI1-I50 P3: on a Receive entry's validate, refuse over-receipts beyond
    pending * (1 + custom_overreceipt_tolerance_pct / 100) per (item, batch).

    Runs on every Stock Entry but no-ops unless custom_original_send_entry is
    set. Tolerance defaults to 0 (strict) when the source field is empty."""
    source_name = _subcontract_source_name(doc)
    if not source_name:
        return

    if not frappe.db.exists("Stock Entry", source_name):
        frappe.throw(_("Original Send entry {0} no longer exists.").format(source_name))

    source = frappe.get_doc("Stock Entry", source_name)
    if source.docstatus != 1:
        frappe.throw(_(
            "Original Send entry {0} is not Submitted (status={1})."
        ).format(source_name, source.docstatus))

    tolerance_pct = flt(source.get("custom_overreceipt_tolerance_pct") or 0)

    # Pending per (item, batch) on the source. NOTE: source.custom_received_qty
    # already excludes THIS draft (we're pre-submit), so we can compare directly.
    pending = {}
    for s in source.items:
        key = _subcontract_match_key(s)
        sent = flt(s.qty)
        already = flt(s.get("custom_received_qty") or 0)
        pending[key] = pending.get(key, 0.0) + (sent - already)

    # Sum incoming receipt per key.
    incoming = {}
    for r in doc.items:
        key = _subcontract_match_key(r)
        incoming[key] = incoming.get(key, 0.0) + flt(r.qty)

    for key, inc in incoming.items():
        pend = pending.get(key, 0.0)
        max_allowed = pend * (1.0 + tolerance_pct / 100.0)
        if inc > max_allowed + _SUBCONTRACT_QTY_EPSILON:
            item_code, batch = key
            frappe.throw(_(
                "Over-receipt blocked for item <b>{0}</b> (batch {1}): "
                "pending {2}, tolerance {3}%, but this entry is taking {4}. "
                "Either reduce the qty on this row or raise "
                "<b>Over-Receipt Tolerance</b> on the source Send entry."
            ).format(item_code, batch or "—",
                     round(pend, 3), tolerance_pct, round(inc, 3)))


@frappe.whitelist()
def apply_subcontract_receipt(doc, method=None):
    """MI1-I50 P3: on_submit of a Receive entry, push received qty back onto
    the source Send entry's items (FIFO across rows with the same key) and
    refresh the source's custom_subcontract_status + each row's
    custom_pending_qty."""
    source_name = _subcontract_source_name(doc)
    if not source_name:
        return
    _apply_receipt_delta(source_name, doc, sign=+1)
    _refresh_subcontract_status(source_name)


@frappe.whitelist()
def revert_subcontract_receipt(doc, method=None):
    """MI1-I50 P3: on_cancel of a Receive entry, pull the previously-applied
    qty back off the source's rows (LIFO) and refresh status."""
    source_name = _subcontract_source_name(doc)
    if not source_name:
        return
    _apply_receipt_delta(source_name, doc, sign=-1)
    _refresh_subcontract_status(source_name)


def _apply_receipt_delta(source_name, receipt_doc, sign):
    """Distribute the receipt's qty across source rows keyed by (item, batch).

    sign=+1 to add (on_submit), sign=-1 to subtract (on_cancel).

    Allocation:
      - Add: walk source rows in declared order, fill each one's remaining
        room (qty - custom_received_qty) first; overflow caused by tolerance
        spills into the first row that had any room.
      - Subtract: walk source rows in reverse, draining already-received
        qty until the cancel amount is fully reversed.

    We use frappe.db.set_value(update_modified=False) to avoid bumping the
    source's modified timestamp from a child-table write — that would cause
    "Document has been modified" errors for any user currently viewing the
    source entry's form."""
    source = frappe.get_doc("Stock Entry", source_name)
    by_key = {}
    for s in source.items:
        by_key.setdefault(_subcontract_match_key(s), []).append(s)

    if sign > 0:
        for r in receipt_doc.items:
            remaining = flt(r.qty)
            if remaining <= 0:
                continue
            rows = by_key.get(_subcontract_match_key(r), [])
            if not rows:
                continue
            # Pass 1: fill rooms in order.
            for s in rows:
                if remaining <= _SUBCONTRACT_QTY_EPSILON:
                    break
                already = flt(s.get("custom_received_qty") or 0)
                room = flt(s.qty) - already
                if room <= 0:
                    continue
                take = min(remaining, room)
                _bump_source_row(s.name, already + take)
                remaining -= take
            # Pass 2: anything left is tolerance overflow → put it on the first row.
            if remaining > _SUBCONTRACT_QTY_EPSILON:
                s = rows[0]
                already = flt(frappe.db.get_value(
                    "Stock Entry Detail", s.name, "custom_received_qty"
                ) or 0)
                _bump_source_row(s.name, already + remaining)
    else:
        for r in receipt_doc.items:
            to_revert = flt(r.qty)
            if to_revert <= 0:
                continue
            rows = by_key.get(_subcontract_match_key(r), [])
            if not rows:
                continue
            for s in reversed(rows):
                if to_revert <= _SUBCONTRACT_QTY_EPSILON:
                    break
                already = flt(frappe.db.get_value(
                    "Stock Entry Detail", s.name, "custom_received_qty"
                ) or 0)
                if already <= 0:
                    continue
                give_back = min(to_revert, already)
                _bump_source_row(s.name, already - give_back)
                to_revert -= give_back
            # Anything still un-reverted means the source was edited
            # post-receipt; clamp to 0 on the first row.
            if to_revert > _SUBCONTRACT_QTY_EPSILON:
                s = rows[0]
                already = flt(frappe.db.get_value(
                    "Stock Entry Detail", s.name, "custom_received_qty"
                ) or 0)
                _bump_source_row(s.name, max(0.0, already - to_revert))


def _bump_source_row(detail_name, new_received_qty):
    frappe.db.set_value(
        "Stock Entry Detail", detail_name,
        "custom_received_qty", flt(new_received_qty),
        update_modified=False,
    )


def _refresh_subcontract_status(source_name):
    """Recompute each source row's custom_pending_qty (qty - custom_received_qty)
    and the parent's custom_subcontract_status."""
    items = frappe.db.sql(
        """
        SELECT name, qty, COALESCE(custom_received_qty, 0) AS recv
        FROM `tabStock Entry Detail`
        WHERE parent = %s AND parenttype = 'Stock Entry'
        """,
        (source_name,),
        as_dict=True,
    )
    total_sent = 0.0
    total_received = 0.0
    for it in items:
        sent = flt(it.qty)
        recv = flt(it.recv)
        pending = sent - recv
        frappe.db.set_value(
            "Stock Entry Detail", it.name,
            "custom_pending_qty", pending,
            update_modified=False,
        )
        total_sent += sent
        total_received += recv

    if total_received <= _SUBCONTRACT_QTY_EPSILON:
        status = _SUBCONTRACT_STATUS_OPEN
    elif total_received + _SUBCONTRACT_QTY_EPSILON >= total_sent:
        status = _SUBCONTRACT_STATUS_FULL
    else:
        status = _SUBCONTRACT_STATUS_PARTIAL

    frappe.db.set_value(
        "Stock Entry", source_name,
        "custom_subcontract_status", status,
        update_modified=False,
    )


def strip_prefix(val):
    """MI1-I62: strip the prefix from values stored as 'Prefix-Value' so
    labels and reports show only the meaningful tail.

    Examples:
        'Grade-AA'        -> 'AA'
        'Lusture-Bright'  -> 'Bright'
        'Wood'            -> 'Wood'    (no hyphen -> unchanged)
        ''                -> ''
        None              -> ''

    Splits on the LAST hyphen (matches the strip_prefix pattern already
    used in container_report.py / stock_sheet reports) so a value like
    'A-B-C' returns 'C'. Grade/Luster in mhr are stored single-hyphen
    ('Grade-AA', 'Lusture-Bright'), so this is correct.
    """
    if not val:
        return ""
    s = str(val)
    if "-" in s:
        return s.rsplit("-", 1)[-1]
    return s


def hty_parse_filament_count(item_code):
    """MI1-I62: extract the filament-count digits from a Den/Fil item code.

    Examples:
        '210/72 7.2 GPD'  -> '72'
        '58D/24F'         -> '24'
        '120D/48 F LOW MX' -> '48'
        '58D/24f'         -> '24'   (case-tolerant)
        'NO-SLASH-CODE'   -> ''

    Strategy: split on '/', take leading digits of the next token, stop
    at the first non-digit. This handles both space-separated ('72 ')
    and letter-suffixed ('24F') forms.
    """
    if not item_code or "/" not in item_code:
        return ""
    after = item_code.split("/", 1)[1].lstrip()
    digits = []
    for ch in after:
        if ch.isdigit():
            digits.append(ch)
        else:
            break
    return "".join(digits)


@frappe.whitelist()
def update_stock_entry(doc, method=None):
    total_cone = 0
    total_qty = 0
    for item in doc.items:
        total_cone += cint(item.custom_cone)
        total_qty += cint(item.qty)
    doc.custom_total_cone = total_cone
    doc.custom_total_qty = total_qty


@frappe.whitelist()
def send_email_after_submit(doc, method=None):
    frappe.sendmail(
        recipients=["", ""],
        subject="Container Submitted",
        message="Container has been submitted successfully",
    )


@frappe.whitelist()
def update_cone_value():
    # update total cone value of the delivery note based on the items and an dcone vlue
    frappe.db.sql(
        """
        UPDATE `tabDelivery Note`
        SET custom_total_cone = (
            SELECT SUM(custom_cone)
            FROM `tabDelivery Note Item`
            WHERE parent = `tabDelivery Note`.name
        )
    """
    )
    frappe.db.commit()

    frappe.db.commit()


@frappe.whitelist()
def set_total_cone(doc, method=None):
    total_cone = 0
    for item in doc.items:
        total_cone += cint(item.custom_cone)
    doc.custom_total_cone = total_cone


@frappe.whitelist()
def same_container():
    containers = frappe.get_all("Container", fields=["*"])
    # data = []
    same_container = []
    for container in containers:
        # //check if container_no is the same
        if container.container_no in same_container:
            continue
        same_container.append(container.container_no)
    return same_container


@frappe.whitelist()
def get_total_closing(container):
    con = frappe.get_doc("Container", container)
    total_closing = 0
    for batch in con.batches:
        total_closing += cint(batch.qty)
    return total_closing


@frappe.whitelist()
def validate_batch(doc, method=None):
    # if frappe.db.exists("Delivery Note", doc.challan_number):
    #     frappe.throw(
    #         f"Delivery Note {doc.challan_number} already exists. Please use a different challan number."
    #     )
    doc.custom_item_length = len(doc.items)
    for item in doc.items:
        if item.batch_no:
            batch = frappe.get_doc("Batch", item.batch_no)

            # Convert all fields to lower case for case-insensitive comparison
            doc_lusture = doc.custom_lusture.lower() if doc.custom_lusture else ""
            batch_lusture = batch.custom_lusture.lower() if batch.custom_lusture else ""
            if doc_lusture != batch_lusture:
                frappe.throw(
                    f"Lusture is not the same as the lusture in Batch {batch.name}"
                )

            doc_grade = doc.custom_grade.lower()
            batch_grade = batch.custom_grade.lower()
            if doc_grade != batch_grade:
                frappe.throw(
                    f"Grade is not the same as the grade in Batch {batch.name}"
                )

            doc_glue = doc.custom_glue.lower() if doc.custom_glue else ""
            batch_glue = batch.custom_glue.lower() if batch.custom_glue else ""
            if doc_glue != batch_glue:
                frappe.throw(f"Glue is not the same as the glue in Batch {batch.name}")

            doc_pulp = doc.custom_pulp.lower() if doc.custom_pulp else ""
            batch_pulp = batch.custom_pulp.lower() if batch.custom_pulp else ""
            if doc_pulp != batch_pulp:
                frappe.throw(f"Pulp is not the same as the pulp in Batch {batch.name}")

            doc_fsc = doc.custom_fsc.lower() if doc.custom_fsc else ""
            batch_fsc = batch.custom_fsc.lower() if batch.custom_fsc else ""
            if doc_fsc != batch_fsc:
                frappe.throw(f"FSC is not the same as the FSC in Batch {batch.name}")

            doc_lot_no = item.custom_lot_no.lower() if item.custom_lot_no else ""
            batch_lot_no = batch.custom_lot_no.lower() if batch.custom_lot_no else ""
            if doc_lot_no != batch_lot_no:
                frappe.throw(
                    f"Lot No is not the same as the lot no in Batch {batch.name}"
                )

            doc_container_no = (
                item.custom_container_no.lower() if item.custom_container_no else ""
            )
            batch_container_no = (
                batch.custom_container_no.lower() if batch.custom_container_no else ""
            )
            if doc_container_no != batch_container_no:
                frappe.throw(
                    f"Container no is not the same as the container no in Batch {batch.name}"
                )

        # set_total_cone(doc)

        # Uncomment and add similar case-insensitive checks if needed for these fields
        # doc_supplier_batch_no = item.custom_supplier_batch_no.lower() if item.custom_supplier_batch_no else ""
        # batch_supplier_batch_no = batch.custom_supplier_batch_no.lower() if batch.custom_supplier_batch_no else ""
        # if doc_supplier_batch_no != batch_supplier_batch_no:
        #     frappe.throw(f'Supplier Batch No is not the same as the supplier batch no in Batch {batch.name}')

        # doc_cone = item.custom_cone.lower() if item.custom_cone else ""
        # batch_cone = batch.custom_cone.lower() if batch.custom_cone else ""
        # if doc_cone != batch_cone:
        #     frappe.throw(f'Cone is not the same as the cone in Batch {batch.name}')


@frappe.whitelist()
def get_delivery_note_batch(
    lot_no=None,
    container_no=None,
    supplier_batch_no=None,
    glue=None,
    pulp=None,
    fsc=None,
    lusture=None,
    grade=None,
    cone=None,
    denier=None,
    is_return=False,
):

    filters = {}

    # Add filters based on available parameters
    if lot_no:
        filters["custom_lot_no"] = lot_no
    if container_no:
        filters["custom_container_no"] = container_no
    if supplier_batch_no:
        filters["custom_supplier_batch_no"] = supplier_batch_no
    if glue:
        filters["custom_glue"] = glue
    if pulp:
        filters["custom_pulp"] = pulp
    if fsc:
        filters["custom_fsc"] = fsc
    if lusture:
        filters["custom_lusture"] = lusture
    if grade:
        filters["custom_grade"] = grade
    if cone and is_return is False:
        filters["custom_cone"] = cone
    if denier and is_return is False:
        filters["item_name"] = denier

    # Check if at least one filter is applied
    if filters:
        if frappe.db.exists("Batch", filters):
            item = frappe.get_doc("Batch", filters)

            return {
                "item_code": item.item,
                "item_name": item.item_name,
                "qty": item.batch_qty,
                "uom": item.stock_uom,
                "batch_no": item.name,
                "supplier_batch_no": item.custom_supplier_batch_no,
                "cone": item.custom_cone,
                "container_no": item.custom_container_no,
                "lot_no": item.custom_lot_no,
                "lusture": item.custom_lusture,
                "grade": item.custom_grade,
                "glue": item.custom_glue,
                "pulp": item.custom_pulp,
                "fsc": item.custom_fsc,
                "notes": item.custom_notes,
            }


@frappe.whitelist()
def get_item_batch(batch):
    if not frappe.db.exists("Batch", batch):
        return {"error": "Batch not found"}

    item = frappe.get_doc("Batch", batch)
    return {
        "item_code": item.item,
        "item_name": item.item_name,
        "qty": item.batch_qty,
        "uom": item.stock_uom,
        "batch_no": item.name,
        "supplier_batch_no": item.custom_supplier_batch_no,
        "cone": item.custom_cone,
        "container_no": item.custom_container_no,
        "lot_no": item.custom_lot_no,
        "lusture": item.custom_lusture,
        "grade": item.custom_grade,
        "glue": item.custom_glue,
        "pulp": item.custom_pulp,
        "fsc": item.custom_fsc,
        "notes": item.custom_notes,
    }


@frappe.whitelist()
def update_item_batch(doc, method=None):
    for item in doc.items:
        if not item.batch_no:
            continue
        if doc.is_return:
            # Always use cone from the original DN item (authoritative source)
            cone_value = 0
            if item.dn_detail:
                cone_value = cint(
                    frappe.db.get_value(
                        "Delivery Note Item", item.dn_detail, "custom_cone"
                    )
                )
            if not cone_value:
                cone_value = cint(item.custom_cone)
            frappe.db.sql(
                """
                UPDATE `tabBatch`
                SET custom_cone = custom_cone + %s
                WHERE name = %s
            """,
                (cone_value, item.batch_no),
            )
        else:
            frappe.db.sql(
                """
                UPDATE `tabBatch`
                SET custom_cone = custom_cone - %s
                WHERE name = %s
            """,
                (cint(item.custom_cone), item.batch_no),
            )


@frappe.whitelist()
def update_batch_warehouse_on_stock_entry(doc, method=None):
    """Propagate target warehouse to Batch.custom_warehouse and parent
    Container.warehouse when a Stock Entry is submitted (Material Transfer
    or any move that sets t_warehouse on items)."""
    _sync_batch_warehouse(doc, use_target=True)


@frappe.whitelist()
def revert_batch_warehouse_on_stock_entry(doc, method=None):
    """On cancel of a Stock Entry, revert Batch + Container warehouse to source."""
    _sync_batch_warehouse(doc, use_target=False)


def _sync_batch_warehouse(doc, use_target=True):
    seen_containers = set()
    for item in doc.items:
        batch_no = item.batch_no
        wh = item.t_warehouse if use_target else item.s_warehouse
        if not batch_no or not wh:
            continue

        frappe.db.set_value(
            "Batch", batch_no, "custom_warehouse", wh, update_modified=False
        )

        parents = frappe.db.sql(
            "SELECT DISTINCT parent FROM `tabBatch Items` WHERE batch_id=%s AND parenttype='Container'",
            batch_no,
        )
        for (parent_name,) in parents:
            if parent_name in seen_containers:
                continue
            seen_containers.add(parent_name)
            frappe.db.set_value(
                "Container",
                parent_name,
                {"warehouse": wh, "set_warehouse": wh},
                update_modified=False,
            )
            frappe.db.sql(
                "UPDATE `tabBatch Items` SET warehouse=%s WHERE parent=%s AND parenttype='Container'",
                (wh, parent_name),
            )


@frappe.whitelist()
def reverse_item_batch(doc, method=None):
    for item in doc.items:
        if not item.batch_no:
            continue
        if doc.is_return:
            cone_value = 0
            if item.dn_detail:
                cone_value = cint(
                    frappe.db.get_value(
                        "Delivery Note Item", item.dn_detail, "custom_cone"
                    )
                )
            if not cone_value:
                cone_value = cint(item.custom_cone)
            frappe.db.sql(
                """
                UPDATE `tabBatch`
                SET custom_cone = custom_cone - %s
                WHERE name = %s
            """,
                (cone_value, item.batch_no),
            )
        else:
            frappe.db.sql(
                """
                UPDATE `tabBatch`
                SET custom_cone = custom_cone + %s
                WHERE name = %s
            """,
                (cint(item.custom_cone), item.batch_no),
            )


@frappe.whitelist()
def get_batches(container_no, lot_no):
    # frappe.msgprint("container_no: {0} lot_no: {1}".format(container_no, lot_no))
    batches = frappe.get_all(
        "Batch",
        filters={"custom_container_no": container_no, "custom_lot_no": lot_no},
        fields=[
            "name",
            "item",
            "item_name",
            "batch_qty",
            "stock_uom",
            "custom_supplier_batch_no",
            "custom_cone",
            "custom_lusture",
            "custom_grade",
            "custom_glue",
            "custom_pulp",
            "custom_fsc",
        ],
    )
    return batches


@frappe.whitelist()
def get_lot_nos(container_no):
    lot_nos = frappe.get_all(
        "Batch", filters={"custom_container_no": container_no}, fields=["custom_lot_no"]
    )
    return lot_nos[0].get("custom_lot_no") if lot_nos else None


@frappe.whitelist()
def get_total_batches(container_no, lot_no):
    batches = get_batches(container_no, lot_no)
    return len(batches)


# def create_purchase_receipt(items, supplier):
#     try:
#         pr = frappe.new_doc("Purchase Receipt")
# pr.supplier = "Jilin Chemical Fiber Stock Co. Ltd"
# pr.posting_date = "2024-05-01"
# pr.set_posting_time = "12:00:00"
# pr.custom_container_no = "MCJC-369"
# pr.custom_lot_number = "29102023"
# for item in items:
#     pr.append("items", {
#         "item_code": item.get('item'),
#         "item_name": item.get('item_name'),
# "qty": item.get('batch_qty'),
# "uom": item.get('stock_uom'),
# "rate": 100,
# "price_list_rate": 100,
# "received_qty": item.get('batch_qty'),
# "conversion_factor": 1,
# "warehouse": "Finished Goods - MC",
# "use_serial_batch_fields": 1,
# "batch_no": item.get('name'),
#             })
#         pr.flags.ignore_permissions = True
#         pr.insert()
#         pr.submit()
#         frappe.db.commit()
#         return f"Purchase Receipt {pr.name} created successfully"
#     except Exception as e:
#         frappe.throw(f"Error creating Purchase Receipt: {e}")
# @frappe.whitelist()
# def get_purchase_items():
#     container_no = "MCJC-369"
#     lot_no = "29102023"
#     batches = get_batches(container_no, lot_no)
#     return batches


@frappe.whitelist()
def create_batch():
    # get the last batch number
    last_batch = frappe.get_last_doc("Batch")
    last_batch_name = last_batch.name
    batch = last_batch_name[3:]
    try:
        batch = frappe.new_doc("Batch")
        batch.item = "120D/30F"
        batch.item_name = "120D/30F"
        batch.batch_qty = 31.6
        batch.stock_uom = "Meter"
        batch.custom_supplier_batch_no = "4825"
        batch.custom_container_no = "MCJC-369"
        batch.custom_lot_no = "29102023"
        batch.custom_lusture = "Dull"
        batch.custom_grade = "AA EVEN"
        batch.custom_glue = "Centrifugal"
        batch.custom_pulp = "Wood"
        batch.custom_fsc = "Mix"
        batch.insert()
        frappe.db.commit()
        return f"Batch {batch.name} created successfully"
    except Exception as e:
        frappe.throw(f"Error creating Batch: {e}")


@frappe.whitelist()
def delete_batches():
    try:
        frappe.db.sql("DELETE FROM `tabContainer`")
        frappe.db.commit()
        return "Batches deleted successfully"
    except Exception as e:
        frappe.throw(f"Error deleting Batches: {e}")


@frappe.whitelist()
def update_batch_stock():
    # Fetch the last 10  batches with their quantities
    batches = frappe.get_all("Update Batch", fields=["batch_id", "batch_quantity"])
    # data = []

    for batch in batches:
        if frappe.db.exists("Batch", batch.get("batch_id")):
            frappe.db.set_value(
                "Batch", batch.get("batch_id"), "batch_qty", batch.get("batch_quantity")
            )
            frappe.db.commit()
    return "Batch stock updated successfully"


@frappe.whitelist()
def delete_docs():
    try:
        frappe.db.sql("DELETE FROM `tabUpdate Batch`")
        frappe.db.commit()
        return "Documents deleted successfully"
    except Exception as e:
        frappe.throw(f"Error deleting Documents: {e}")


@frappe.whitelist()
def print_selected_docs(doctype, docnames):
    import json
    from frappe.utils.print_format import download_multi_pdf

    docnames = json.loads(docnames)
    doctype_dict = {doctype: docnames}

    pdf_data = download_multi_pdf(
        doctype_dict,
        doctype,
        "Batch",
        no_letterhead=False,
        letterhead=None,
        options=None,
    )
    return pdf_data


@frappe.whitelist()
def generate_multi_pdf_url(batches, doc_name):
    name = "Batch"
    # batches = []
    # for b in self.list_batches:
    #     batches.append(b.batch)

    doctype = {"Batch": batches}

    try:
        format = "Batch"
        download_multi_pdf(doctype, name, format)
        pdf_content = frappe.local.response.filecontent

        if not pdf_content:
            raise ValueError("PDF content is empty or not generated correctly.")

        # Construct the filename
        name_str = name.replace(" ", "-").replace("/", "-")
        filename = f"combined_{name_str}.pdf"

        # Save the PDF content as a File document in the database
        _file = frappe.get_doc(
            {
                "doctype": "File",
                "file_name": filename,
                "is_private": 0,
                "content": pdf_content,
            }
        )
        _file.save()
        frappe.db.commit()
        file_url = _file.file_url
        frappe.db.set_value("Print Batch", doc_name, "file_url", file_url)
        # reload the form
        frappe.msgprint(
            f"PDF generated successfully. <a href='{file_url}' target='_blank'>Click here</a> to print the PDF."
        )

    except Exception as e:
        frappe.log_error(f"Error generating PDF URL: {str(e)}")
        frappe.throw(f"Failed to generate PDF: {str(e)}")


@frappe.whitelist()
def get_print_batch(lot_no, container_no, supplier_batch_no=None, item=None, cone=None):
    """MI1-I27 / MI1-I62: return Batch rows matching (container, lot) + optional filters.

    Raj's report (MCJC-1522 / Lot 13112025): the same supplier_batch_no
    can have multiple Batch records under one (container, lot), each
    with a different item / denier. The previous implementation used
    `frappe.get_doc(..., filters)` which only returns the first match,
    so when the user added a "batch" to print, only one denier ever
    landed in the list and the others silently disappeared.

    Now returns a list of every matching Batch. The caller (JS in
    print_batch.js `fetch_and_append_batch`) iterates and adds one row
    per Batch. If the trio matches just one Batch — single-element
    list — caller still works.

    MI1-I27 (Item bifurcation): when `item` is supplied, restrict to
    that one item so a Container + Lot holding two deniers can be
    printed one item at a time instead of combined. Blank/None `item`
    keeps the old behaviour (all items for the trio).

    MI1-I62 (Fetch by Container+Lot): `supplier_batch_no` is now
    OPTIONAL. With blank/None supplier_batch_no, every Batch under the
    given (container, lot) is returned. Passing a value still narrows
    to that supplier batch (existing behaviour preserved).

    MI1-I62 (VFY Cone fetch, 2026-06-23): `cone` is OPTIONAL. When set,
    only Batches whose `custom_cone` equals the given value are
    returned — used by the VFY-only "Cone" form field. Combine with
    container + lot for the typical "all batches of cone N for this
    (container, lot)" lookup.
    """
    filters = {
        "custom_lot_no": lot_no,
        "custom_container_no": container_no,
    }
    if supplier_batch_no:
        filters["custom_supplier_batch_no"] = supplier_batch_no
    if item:
        filters["item"] = item
    if cone:
        filters["custom_cone"] = cone
    rows = frappe.get_all(
        "Batch",
        filters=filters,
        fields=["name", "item", "custom_cone", "custom_lot_no", "batch_qty"],
        order_by="creation asc",
    )
    return [
        {
            "item": r.item,
            "batch": r.name,
            "cone": r.custom_cone,
            "lot_no": r.custom_lot_no,
            "batch_qty": r.batch_qty,
        }
        for r in rows
    ]


@frappe.whitelist()
def get_container_ids_for(container_no, lot_no, item=None):
    """MI1-I62 (Container ID fetch, 2026-06-23): return the distinct
    Container document IDs that match the given (container_no, lot_no, item).

    Background: Container's autoname is `format:{container_no}-{#}`, so
    one user-facing Container No (e.g. MCJC-1614) maps to MANY Container
    documents (MCJC-1614-1, MCJC-1614-2, ...). The Print Batch form's
    "Fetch by Container ID" flow uses this method to populate a Select
    so the user can pick exactly one of those Container docs and then
    fetch every Batch belonging to it.

    Returns a list of strings (sorted). `item` is optional — pass to
    narrow when one (container_no, lot) has rows under different items.
    """
    if not (container_no and lot_no):
        return []
    filters = {
        "container_no": container_no,
        "lot_no": lot_no,
    }
    if item:
        filters["item"] = item
    rows = frappe.get_all(
        "Container",
        filters=filters,
        fields=["name"],
        order_by="name asc",
        # The Container table is large; an explicit limit keeps the
        # popup usable when someone filters too broadly.
        limit=200,
    )
    return [r.name for r in rows]


@frappe.whitelist()
def get_batches_for_container_id(container_id):
    """MI1-I62 (Container ID fetch, 2026-06-23): return every Batch
    belonging to one specific Container document, in the same payload
    shape as `get_print_batch` so the JS append/dedup path is reused.

    The link from Container -> Batch lives in the `Batch Items` child
    table (parent=Container.name, batch_id=Batch.name). We join that
    bridge to the Batch master to pull the cone / lot / qty / supplier
    batch info the print-batch UI needs.
    """
    if not container_id:
        return []
    rows = frappe.db.sql(
        """
        SELECT
            b.name        AS batch,
            b.item        AS item,
            b.custom_cone AS cone,
            b.custom_lot_no AS lot_no,
            b.batch_qty   AS batch_qty,
            b.custom_supplier_batch_no AS supplier_batch_no
        FROM `tabBatch Items` bi
        INNER JOIN `tabBatch` b ON b.name = bi.batch_id
        WHERE bi.parent = %s AND bi.parenttype = 'Container'
        ORDER BY b.creation ASC
        """,
        (container_id,),
        as_dict=True,
    )
    return rows


@frappe.whitelist()
def update_container():
    frappe.db.sql(
        """
        UPDATE `tabContainer` 
        SET total_net_weight = (
            SELECT SUM(qty) 
            FROM `tabBatch Items` 
            WHERE parent = tabContainer.name
        )
    """
    )
    frappe.db.commit()
    return "Container updated successfully"


@frappe.whitelist()
def update_all_containers_batch_qty():
    # Bulk variant: sync batch_qty for EVERY Container's batches. (Renamed
    # from update_container_batch_qty to resolve a duplicate-definition clash
    # with the single-container variant below, which silently shadowed this.)
    message = []
    containers = frappe.get_all("Container", fields=["name"])
    for container in containers:
        container_doc = frappe.get_doc("Container", container.name)
        for batch in container_doc.batches:
            frappe.db.set_value("Batch", batch.batch_id, "batch_qty", batch.qty)
            frappe.db.commit()
        message.append(f"Container {container.name} updated successfully")
    return message


@frappe.whitelist()
def resend_email_queue():
    from frappe.email.doctype.email_queue.email_queue import send_now

    emails = frappe.get_all("Email Queue", {"status": "Not Sent"})
    for email in emails:
        send_now(email.name)
    return "Email Queue updated successfully"


@frappe.whitelist()
def update_custom_item_length():
    # update th custom_item_length in the delivery note
    notes = frappe.get_all("Delivery Note", fields=["name"])
    for note in notes:
        doc = frappe.get_doc("Delivery Note", note.name)
        frappe.db.set_value(
            "Delivery Note", note.name, "custom_item_length", len(doc.items)
        )
        frappe.db.commit()


@frappe.whitelist()
def create_batches(container):
    frappe.publish_realtime(
        "site_creation", {"message": "Creating Batches"}, user=frappe.session.user
    )
    container_doc = frappe.get_doc("Container", container)

    # HTY captures specs under colour/product/type; fold them back into the
    # canonical glue/lusture/pulp columns so Batches read the same fields as
    # VFY (mirror of Container.resolved_specs()).
    if container_doc.transaction_type == "HTY":
        specs = {
            "glue": container_doc.product,
            "lusture": container_doc.colour,
            "pulp": container_doc.type,
        }
    else:
        specs = {
            "glue": container_doc.glue,
            "lusture": container_doc.lusture,
            "pulp": container_doc.pulp,
        }

    for batch in container_doc.batches:
        if frappe.db.exists("Batch", batch.batch_id):
            continue
        else:
            batch_doc = frappe.new_doc("Batch")
            batch_doc.item = batch.item
            batch_doc.batch_qty = batch.qty
            batch_doc.stock_uom = batch.uom
            batch_doc.batch_id = batch.batch_id
            batch_doc.custom_supplier_batch_no = batch.supplier_batch_no
            batch_doc.custom_container_no = container_doc.container_no
            batch_doc.custom_cone = batch.cone
            batch_doc.custom_glue = specs["glue"]
            batch_doc.custom_lusture = specs["lusture"]
            batch_doc.custom_grade = container_doc.grade
            batch_doc.custom_pulp = specs["pulp"]
            batch_doc.custom_fsc = container_doc.fsc
            batch_doc.custom_lot_no = container_doc.lot_no
            batch_doc.save(ignore_permissions=True)
            batch_doc.submit()
    create_purchase_receipt(container_doc.name)
    frappe.db.commit()


def create_purchase_receipt(container):
    # Fetch the Container document using the passed container ID
    container_doc = frappe.get_doc("Container", container)

    items = container_doc.get_items()

    # Create a new Purchase Receipt document
    purchase_receipt = frappe.new_doc("Purchase Receipt")
    purchase_receipt.company = container_doc.company
    purchase_receipt.supplier = container_doc.supplier
    purchase_receipt.posting_date = container_doc.posting_date
    purchase_receipt.custom_container_no = container_doc.name
    purchase_receipt.custom_total_batches = len(container_doc.batches)
    purchase_receipt.items = []

    # Add items to the Purchase Receipt
    for item in items:
        serial_and_batch_bundle = container_doc.create_serial_and_batch_bundle(
            item["item"], "Inward"
        )
        # Skip items that returned None (serial number items or non-batch items)
        if serial_and_batch_bundle is None:
            continue
        # Check if create_serial_and_batch_bundle returned an error dict
        if isinstance(serial_and_batch_bundle, dict):
            frappe.throw(
                f"Failed to create Serial and Batch Bundle for item {item['item']}: {serial_and_batch_bundle.get('error', 'Unknown error')}"
            )
        purchase_receipt.append(
            "items",
            {
                "item_code": item["item"],
                "item_name": item["item"],
                "qty": item["batch_qty"],
                "stock_uom": item["stock_uom"],
                "warehouse": container_doc.set_warehouse,
                "allow_zero_valuation_rate": 1,
                "rate": 100,
                "price_list_rate": 100,
                "received_qty": item["batch_qty"],
                "conversion_factor": 1,
                "use_serial_batch_fields": 0,
                "serial_and_batch_bundle": serial_and_batch_bundle,
            },
        )

    # Save and submit the Purchase Receipt
    try:
        purchase_receipt.save()
        purchase_receipt.submit()
        frappe.db.commit()
        return purchase_receipt.name
    except Exception as e:
        frappe.db.rollback()
        frappe.log_error(frappe.get_traceback(), "create_purchase_receipt")
        frappe.msgprint(
            {"message": "Failed to create Purchase Receipt", "error": str(e)}
        )


@frappe.whitelist()
def update_pr_with_container_details():
    frappe.db.sql(
        """
        UPDATE `tabPurchase Receipt` AS pr
        JOIN `tabContainer` AS c ON pr.custom_container_no = c.name
        SET 
            pr.custom_lot_number = c.lot_no,
            pr.custom_lusture = c.lusture,
            pr.custom_glue = c.glue,
            pr.custom_grade = c.grade,
            pr.custom_pulp = c.pulp,
            pr.custom_fsc = c.fsc,
            pr.custom_merge_no = c.merge_no,
            pr.custom_notes = c.notes
    """
    )
    frappe.db.commit()


update_pr_with_container_details()


@frappe.whitelist()
def delete_doc(doctype, name):
    frappe.db.sql(f"DELETE FROM `tab{doctype}` WHERE name = %s", (name,))
    frappe.db.commit()
    return "Success"


def update_batch_qty():
    container = frappe.get_all("Container", {"docstatus": 1}, ["name"])
    for container in container:
        container_doc = frappe.get_doc("Container", container.name)
        for batch in container_doc.batches:
            # update batch if batch id exists and container no is the same adn lot no is the same
            frappe.db.sql(
                f"UPDATE `tabBatch` SET batch_qty = {batch.qty} WHERE name = '{batch.batch_id}' AND custom_container_no = '{container_doc.container_no}' AND custom_lot_no = '{container_doc.lot_no}'"
            )
            frappe.db.commit()
    return "Success"


@frappe.whitelist()
def enqueue_update_batch_qty():
    frappe.enqueue("mhr.utilis.update_batch_qty", queue="long")
    return "Success"


@frappe.whitelist()
def update_container_batch_qty(container: str):
    container_doc = frappe.get_doc("Container", container)
    for batch in container_doc.batches:
        frappe.db.sql(
            f"UPDATE `tabBatch` SET batch_qty = {batch.qty} WHERE name = '{batch.batch_id}'"
        )
        frappe.db.commit()
    return "Success"


@frappe.whitelist()
def set_return_cone_from_original(doc, method=None):
    if not doc.is_return or not doc.return_against:
        return
    for item in doc.items:
        if item.dn_detail and not cint(item.custom_cone):
            original_cone = cint(
                frappe.db.get_value("Delivery Note Item", item.dn_detail, "custom_cone")
            )
            if original_cone:
                item.custom_cone = original_cone


@frappe.whitelist()
def set_delivery_note_user(doc, method=None):
    doc.prepared_by = frappe.session.user


def calculate_delivery_note_totals(doc, method=None):
    total_cone = 0
    for item in doc.items:
        total_cone += cint(item.custom_cone or 0)
    doc.custom_total_cone = total_cone
    doc.custom_item_length = len(doc.items)
    set_header_container_info_from_items(doc)


def set_header_container_info_from_items(doc):
    """Populate DN header container fields from item rows (and their Batch docs).

    If rows share a single value, set it on the header; if rows span multiple
    values, comma-join the distinct values. Only fills header fields that are
    currently empty so manual edits aren't clobbered.
    """
    if not doc.get("items"):
        return

    # Pull batch attributes once per unique batch_no
    batch_nos = list({i.batch_no for i in doc.items if i.get("batch_no")})
    batch_cache = {}
    if batch_nos:
        for b in frappe.get_all(
            "Batch",
            filters={"name": ["in", batch_nos]},
            fields=[
                "name",
                "item",
                "custom_glue",
                "custom_pulp",
                "custom_lusture",
                "custom_grade",
                "custom_fsc",
            ],
        ):
            batch_cache[b.name] = b

    def _distinct(values):
        seen = []
        for v in values:
            if v in (None, ""):
                continue
            if v not in seen:
                seen.append(v)
        return seen

    def _collapse(values):
        vals = _distinct(values)
        if not vals:
            return ""
        if len(vals) == 1:
            return vals[0]
        return ", ".join(str(v) for v in vals)

    # Row-level fields already on Delivery Note Item
    row_containers = [i.get("custom_container_no") for i in doc.items]
    row_lots = [i.get("custom_lot_no") for i in doc.items]

    # Batch-level fields — pull via batch_no
    batch_glues, batch_pulps, batch_lustres, batch_grades, batch_fscs, batch_items = (
        [],
        [],
        [],
        [],
        [],
        [],
    )
    for i in doc.items:
        b = batch_cache.get(i.get("batch_no"))
        if not b:
            continue
        batch_glues.append(b.get("custom_glue"))
        batch_pulps.append(b.get("custom_pulp"))
        batch_lustres.append(b.get("custom_lusture"))
        batch_grades.append(b.get("custom_grade"))
        batch_fscs.append(b.get("custom_fsc"))
        batch_items.append(b.get("item"))

    mapping = {
        "custom_container_no": _collapse(row_containers),
        "custom_lot_no": _collapse(row_lots),
        "custom_glue": _collapse(batch_glues),
        "custom_pulp": _collapse(batch_pulps),
        "custom_lusture": _collapse(batch_lustres),
        "custom_grade": _collapse(batch_grades),
        "custom_fsc": _collapse(batch_fscs),
        "custom_denier": _collapse(batch_items),
    }

    for fieldname, value in mapping.items():
        if value and not doc.get(fieldname):
            doc.set(fieldname, value)


@frappe.whitelist()
def rename_delivery_note():
    delivery_notes = frappe.get_all("Delivery Trip", ["name", "challan_number"])

    # Find the highest existing challan number
    max_challan = 0
    for dn in delivery_notes:
        if dn.challan_number:
            try:
                challan_int = int(dn.challan_number)
                max_challan = max(max_challan, challan_int)
            except (ValueError, TypeError):
                continue

    for delivery_note in delivery_notes:
        challan_no = delivery_note.challan_number

        try:
            if not challan_no:
                # For empty challan, use next available number after max_challan
                max_challan += 1
                new_name = str(max_challan)

                # Keep incrementing until we find an unused number
                while frappe.db.exists("Delivery Trip", new_name) or frappe.db.exists(
                    "Delivery Trip", {"challan_number": new_name}
                ):
                    max_challan += 1
                    new_name = str(max_challan)
            else:
                # Use existing challan number logic
                new_name = challan_no
                counter = int(challan_no)

                while frappe.db.exists("Delivery Trip", new_name):
                    counter += 1
                    new_name = str(counter)

                    while frappe.db.exists(
                        "Delivery Trip", {"challan_number": new_name}
                    ):
                        counter += 1
                        new_name = str(counter)

            # Update both the document name and challan_number
            frappe.db.sql(
                """
                UPDATE `tabDelivery Trip` 
                SET name = %s, challan_number = %s 
                WHERE name = %s
            """,
                (new_name, new_name, delivery_note.name),
            )
            frappe.db.commit()
        except (ValueError, TypeError):
            continue

    return "Success"


@frappe.whitelist()
def autoname(doc, method=None):
    doc.name = doc.challan_number


@frappe.whitelist()
def check_batch_already_used_in_delivery_note(batch_no):
    """
    Checks if a batch is already used in any Delivery Note Item

    Args:
        batch_no (str): The batch number to check

    Returns:
        dict: A dictionary with 'used' status and delivery note name if used
    """
    if not batch_no:
        return {"used": False}

    # Check if the batch exists in any Delivery Note Item that is part of a submitted Delivery Note
    delivery_note = frappe.db.sql(
        """
        SELECT batch_no 
        FROM `tabDelivery Note Item` 
        WHERE batch_no = %s 
        LIMIT 1
    """,
        batch_no,
        as_dict=True,
    )

    if delivery_note:
        return {"used": True, "delivery_note": delivery_note[0].batch_no}

    return {"used": False}


@frappe.whitelist()
def validate_delivery_note_batches(doc, method=None):
    """
    Validates that none of the batches in a Delivery Note are already used in other submitted Delivery Notes

    Args:
        doc: The Delivery Note document
        method: The trigger method (validate, before_save, etc.)
    """
    # if it is not a return then apply validation if not then don't apply validation
    if doc.is_return is False:
        for item in doc.items:
            if item.batch_no:
                # Check if this batch is used in any other Delivery Note Item
                exists = frappe.db.sql(
                    """
                    SELECT name FROM `tabDelivery Note Item`
                    WHERE batch_no = %s AND parent != %s
                    LIMIT 1
                """,
                    (item.batch_no, doc.name),
                    as_dict=1,
                )

                if exists:
                    frappe.throw(
                        _(
                            "Batch {0} is already used. Please select a different batch."
                        ).format(item.batch_no)
                    )


@frappe.whitelist()
def get_number_of_boxes(container_name):
    # for the number of boxes select only the batches that have the same cone and contianer nad have batch_qty more than 0 from batch doctype
    # return frappe.db.count(
    #     "Batch",
    #     {
    #         "custom_container_no": container_name,
    #         "batch_qty": (">", 0),
    #     },
    # )
    query = """
        SELECT COUNT(*) as count
        FROM `tabBatch` b
        WHERE b.custom_container_no = %s AND b.batch_qty > 0
    """
    result = frappe.db.sql(query, (container_name,), as_dict=1)
    return result[0].count if result else 0


@frappe.whitelist()
def update_container_item():
    frappe.db.sql(
        """
    UPDATE `tabBatch Items` cb
    JOIN `tabContainer` c ON cb.parent = c.name
    SET cb.item = c.item
    WHERE cb.parenttype = 'Container' AND cb.parentfield = 'batches'
    """
    )
    frappe.db.commit()
    return "successfully update batches"


@frappe.whitelist()
def submit_docs(doctype):
    docs = frappe.get_all(doctype, {"docstatus": 0})
    successful = []
    failed = []

    for doc in docs:
        try:
            d = frappe.get_doc(doctype, doc.name)

            d.submit()
            frappe.db.commit()
            successful.append(doc.name)
        except Exception as e:
            # Log the error with full details
            error_message = f"Error submitting {doctype} {doc.name}: {str(e)}"
            frappe.log_error(
                message=error_message,
                title=f"Error submitting {doctype}",
                reference_doctype=doctype,
                reference_name=doc.name,
            )
            failed.append({"name": doc.name, "error": str(e)})
            # Rollback any partial changes for this document
            frappe.db.rollback()
            # Continue processing other documents
            continue

    # Prepare summary message
    total = len(docs)
    success_count = len(successful)
    failed_count = len(failed)

    summary = f"Processed {total} {doctype}(s): {success_count} submitted successfully, {failed_count} failed"

    if failed:
        summary += f". Failed documents: {', '.join([f['name'] for f in failed])}"

    return summary


@frappe.whitelist()
def enqueue_submit_docs(doctype):
    frappe.enqueue("mhr.utilis.submit_docs", doctype=doctype, queue="long")
    return "docs submitted successfully"


@frappe.whitelist()
def cancel_receipts():
    # cancel receipts create on or before 21-05-2025 18:58:43
    docs = frappe.get_all(
        "Purchase Receipt", {"docstatus": 1, "creation": ("<", "2025-05-21 18:58:43")}
    )
    for doc in docs:
        d = frappe.get_doc("Purchase Receipt", doc.name)
        d.cancel()
        frappe.db.commit()
    return "receipts cancelled successfully"


@frappe.whitelist()
def enqueue_cancel_receipts():
    frappe.enqueue("mhr.utilis.cancel_receipts", queue="long")
    return "receipts cancelled successfully"


@frappe.whitelist()
def validate_so_available_qty(doc, method=None):
    """Prevent overbooking: ensure SO item qty does not exceed available stock for batches."""
    for item in doc.items:
        if not item.custom_batch_no:
            continue

        # Get current stock balance for this batch
        batch_balance = flt(
            frappe.db.get_value("Batch", item.custom_batch_no, "batch_qty")
        )

        # Get total already-booked qty from other submitted SOs (excluding this one)
        already_booked = frappe.db.sql(
            """
            SELECT COALESCE(SUM(soi.qty - soi.delivered_qty), 0)
            FROM `tabSales Order Item` soi
            JOIN `tabSales Order` so ON so.name = soi.parent
            WHERE soi.custom_batch_no = %s
            AND so.docstatus = 1
            AND so.name != %s
            AND so.status IN ('To Deliver and Bill', 'To Deliver', 'To Bill', 'Partially Delivered')
        """,
            (item.custom_batch_no, doc.name),
        )[0][0]

        available = flt(batch_balance) - flt(already_booked)
        requested = flt(item.qty)

        if requested > available:
            frappe.throw(
                _(
                    "Row {0}: Batch {1} has only {2} kg available ({3} kg in stock, {4} kg already booked). "
                    "You are trying to book {5} kg."
                ).format(
                    item.idx,
                    item.custom_batch_no,
                    round(available, 2),
                    round(batch_balance, 2),
                    round(flt(already_booked), 2),
                    round(requested, 2),
                )
            )


@frappe.whitelist()
def validate_batch_container_match(doc, method=None):
    """
    Validates that all batches in Serial and Batch Bundles belong to the same container
    as specified in the Delivery Note header.

    This prevents batches from Container A being used in a Delivery Note meant for Container B.
    """
    # Skip if no container is specified in the Delivery Note
    if not doc.custom_container_no:
        return

    # Skip for returns
    if doc.is_return:
        return

    mismatched_batches = []

    for item in doc.items:
        # Check Serial and Batch Bundle
        if item.serial_and_batch_bundle:
            # Get all batches in this bundle
            bundle_entries = frappe.get_all(
                "Serial and Batch Entry",
                filters={"parent": item.serial_and_batch_bundle},
                fields=["batch_no"],
            )

            for entry in bundle_entries:
                if entry.batch_no:
                    # Get the batch's container_no
                    batch_container = frappe.db.get_value(
                        "Batch", entry.batch_no, "custom_container_no"
                    )

                    # Check if it matches the items container no
                    if batch_container:
                        if (
                            batch_container
                            and batch_container != item.custom_container_no
                        ):
                            mismatched_batches.append(
                                {
                                    "batch": entry.batch_no,
                                    "batch_container": batch_container,
                                    "dn_container": item.custom_container_no,
                                }
                            )

        # Also check direct batch_no field if populated
        if item.batch_no:
            batch_container = frappe.db.get_value(
                "Batch", item.batch_no, "custom_container_no"
            )

            if batch_container and batch_container != doc.custom_container_no:
                mismatched_batches.append(
                    {
                        "batch": item.batch_no,
                        "batch_container": batch_container,
                        "dn_container": item.custom_container_no,
                    }
                )

    # If there are mismatched batches, throw an error
    if mismatched_batches:
        error_details = "<br>".join(
            [
                f"Batch <b>{m['batch']}</b> belongs to Container <b>{m['batch_container']}</b>"
                for m in mismatched_batches[:5]  # Show first 5
            ]
        )

        if len(mismatched_batches) > 5:
            error_details += f"<br>... and {len(mismatched_batches) - 5} more"

        frappe.throw(
            _(
                "Cannot save Delivery Note. The following batches do not belong to "
                "Container <b>{0}</b>:<br><br>{1}<br><br>"
                "Please select batches from the correct container."
            ).format(doc.custom_container_no, error_details),
            title=_("Container Mismatch"),
        )


# ----------------------------------------------------------------------
# MI1-I26 — Submit Stock Entry in background.
#
# A Material Transfer with 245 batches takes 60+ seconds for ERPNext to
# create all SLEs / Bins, and gunicorn kills the HTTP request before
# submit() returns. The user sees "Request Timeout".
#
# This endpoint enqueues the submit on a worker. Page returns
# immediately; a realtime event fires when the submit lands so the
# form reloads itself.
# ----------------------------------------------------------------------
@frappe.whitelist()
def submit_stock_entry_in_background(name):
    """Queue the Stock Entry submit on a background worker.

    Returns immediately so the HTTP layer doesn't time out.
    """
    if not name:
        frappe.throw("Stock Entry name is required.")
    doc = frappe.get_doc("Stock Entry", name)
    if doc.docstatus != 0:
        frappe.throw(
            f"Stock Entry {name} is not in Draft. Current docstatus={doc.docstatus}."
        )
    frappe.enqueue(
        method="mhr.utilis._submit_stock_entry_worker",
        queue="long",
        timeout=900,
        job_name=f"mhr-submit-stock-entry-{name}",
        name=name,
        notify_user=frappe.session.user,
    )
    return {"queued": True, "name": name}


def _submit_stock_entry_worker(name, notify_user):
    """Worker: load + submit. Publishes realtime when done."""
    ok = False
    error = ""
    try:
        doc = frappe.get_doc("Stock Entry", name)
        if doc.docstatus == 0:
            doc.submit()
            frappe.db.commit()
        ok = True
    except Exception as exc:
        frappe.db.rollback()
        error = str(exc)
        frappe.log_error(
            frappe.get_traceback(),
            f"mhr submit_stock_entry_worker failed for {name}",
        )
    frappe.publish_realtime(
        event="mhr_stock_entry_submitted",
        message={"name": name, "ok": ok, "error": error},
        user=notify_user,
    )


# ---------------------------------------------------------------------------
# MI1-I39 P2-G — Server-side HTY hooks
# ---------------------------------------------------------------------------
# These run via doc_events wired in hooks.py. Each is gated on
# transaction_type == 'HTY' so VFY-mode flow stays identical
# (FRD's hard rule).
#
# Behaviors:
#   - Stock Entry validate: if HTY and naming_series is non-HTY/missing,
#     set the HTY series prefix. Belt-and-braces with the Client Script.
#   - Delivery Trip validate: if all linked Delivery Notes are HTY-mode,
#     auto-flip the Trip's transaction_type to HTY (and HTY naming series).
#   - Delivery Note on_submit (HTY return only): mhr's reverse_item_batch
#     already restores cones on the original Container's batches when a
#     DN is CANCELLED. For HTY-mode Returns, we mirror that on SUBMIT —
#     a fresh return DN re-credits the cone count to its source batches.


def validate_hty_stock_entry(doc, method=None):
    """Stock Entry validate hook (HTY-aware).
    If transaction_type=HTY and the user hasn't picked an HTY naming
    series, set the default one. Skip if already submitted."""
    if getattr(doc, "docstatus", 0) != 0:
        return
    if (getattr(doc, "transaction_type", None) or "VFY") != "HTY":
        return
    series = getattr(doc, "naming_series", "") or ""
    if not series.startswith("HTY-"):
        # First HTY series option for Stock Entry — keep in sync with the
        # Property Setter populated by the P2-F setup. If the option no
        # longer exists, Frappe will validate-error on save (intentional).
        doc.naming_series = "HTY-STE-.YYYY.-"


def fill_default_addresses_on_delivery_trip(doc, method=None):
    """MI1-I31 — Delivery Trip validate hook.

    Two passes, both per Delivery Stop:

      Pass 1 (auto-FETCH): For each Stop with a customer set but no
      address, fall back to the customer's primary address (Frappe's
      get_default_address).

      Pass 2 (auto-LINK, MI1-I31 v2 per Raj's follow-up): For each Stop
      with BOTH customer AND address set, ensure tabDynamic Link has a
      row linking that Address back to the Customer. Without this row,
      Frappe.contacts.get_default_address can't find the Address next
      time — which is why Raj kept having to re-enter the same address.
      Idempotent: skips when the link already exists.

    Belt-and-braces alongside the Client Script — covers Trips created
    via API / import / server-script that bypass the form-level handler.
    """
    from frappe.contacts.doctype.address.address import (
        get_default_address,
        get_address_display,
    )

    stops = getattr(doc, "delivery_stops", None) or []
    for stop in stops:
        if not getattr(stop, "customer", None):
            continue

        # ---- Pass 1: auto-fetch default address ----
        if not getattr(stop, "address", None):
            addr = get_default_address("Customer", stop.customer)
            if addr:
                stop.address = addr
                if not getattr(stop, "customer_address", None):
                    try:
                        stop.customer_address = get_address_display(addr)
                    except Exception:
                        # get_address_display can raise on missing Address
                        # rows; never block the save for a display string.
                        frappe.log_error(
                            message=frappe.get_traceback(),
                            title="MI1-I31: get_address_display failed",
                        )

        # ---- Pass 2: auto-link Address ↔ Customer ----
        if getattr(stop, "address", None) and stop.customer:
            _ensure_address_customer_link(stop.address, stop.customer)


def _ensure_address_customer_link(address_name, customer):
    """Ensure a tabDynamic Link row exists tying the given Address to
    the given Customer. If it doesn't, append one to the Address doc
    and save. Idempotent + safe — never overwrites existing links."""
    # Cheap pre-check: avoid loading the Address doc if the link
    # already exists.
    if frappe.db.exists(
        "Dynamic Link",
        {
            "parent": address_name,
            "parenttype": "Address",
            "parentfield": "links",
            "link_doctype": "Customer",
            "link_name": customer,
        },
    ):
        return
    try:
        addr_doc = frappe.get_doc("Address", address_name)
    except frappe.DoesNotExistError:
        # Stale stop.address pointing at a deleted Address row; nothing
        # to link. Skip silently — the Stop save can still succeed.
        return
    addr_doc.append(
        "links",
        {
            "link_doctype": "Customer",
            "link_name": customer,
        },
    )
    try:
        addr_doc.save(ignore_permissions=True)
    except Exception:
        # Address.save() can fail on missing mandatory fields if the
        # Address was created via a Delivery Stop's minimal picker.
        # Log but don't block the Trip save.
        frappe.log_error(
            message=frappe.get_traceback(),
            title=f"MI1-I31: failed to link Address {address_name} → Customer {customer}",
        )


def validate_hty_delivery_trip(doc, method=None):
    """Delivery Trip validate hook (HTY-aware).
    If every linked Delivery Note is HTY-mode, automatically flip the
    Trip to HTY mode + HTY naming series. Mixed trips (some HTY, some
    VFY) stay VFY — no surprise side effects."""
    if getattr(doc, "docstatus", 0) != 0:
        return
    stops = getattr(doc, "delivery_stops", None) or []
    dn_names = [s.delivery_note for s in stops if getattr(s, "delivery_note", None)]
    if not dn_names:
        return
    placeholders = ", ".join(["%s"] * len(dn_names))
    rows = frappe.db.sql(
        f"""
        SELECT name, IFNULL(transaction_type, 'VFY') AS tt
        FROM `tabDelivery Note`
        WHERE name IN ({placeholders})
        """,
        tuple(dn_names),
        as_dict=True,
    )
    if not rows or any(r.tt != "HTY" for r in rows):
        return
    # All HTY → propagate.
    if (getattr(doc, "transaction_type", None) or "VFY") != "HTY":
        doc.transaction_type = "HTY"
    series = getattr(doc, "naming_series", "") or ""
    if not series.startswith("HTY-"):
        doc.naming_series = "HTY-DT-.YYYY.-"


def restore_cones_for_hty_return(doc, method=None):
    """Delivery Note on_submit hook (HTY return only).
    Mirrors `reverse_item_batch` for HTY-mode return DNs at submit time:
    for each item row with a custom_cone value, add the cone count back
    onto the original Container's Batch Items child row. The Batch
    master's `batch_qty` is reconciled by ERPNext's standard return SLE,
    so we only touch the mhr-specific cone tracking.

    Why on submit (not cancel like the original Meher flow): an HTY
    return represents physical cones coming back in. They should be
    re-credited the moment the return is recorded — symmetric with how
    a normal sale debits them at submit."""
    if not getattr(doc, "is_return", 0):
        return
    if (getattr(doc, "transaction_type", None) or "VFY") != "HTY":
        return

    for item in doc.items or []:
        cone = cint(getattr(item, "custom_cone", 0))
        batch_no = getattr(item, "batch_no", None)
        container_no = getattr(item, "custom_container_no", None)
        if not (cone and batch_no and container_no):
            continue
        # Find the Batch Items child row (parent=Container doc, batch_id=batch_no).
        rows = frappe.db.sql(
            """
            SELECT bi.name AS row_name, bi.parent AS container, bi.cone AS cur_cone
            FROM `tabBatch Items` bi
            JOIN `tabContainer` c ON c.name = bi.parent
            WHERE bi.batch_id = %s
              AND c.container_no = %s
              AND c.docstatus = 1
              AND bi.parenttype = 'Container'
            LIMIT 1
            """,
            (batch_no, container_no),
            as_dict=True,
        )
        if not rows:
            continue
        row = rows[0]
        new_cone = cint(row.cur_cone) + cone
        frappe.db.set_value("Batch Items", row.row_name, "cone", new_cone)
    frappe.db.commit()


# ---------------------------------------------------------------------------
# MI1-I39 — HTY transaction_type helpers shared across reports
# ---------------------------------------------------------------------------
# Each existing stock report queries `tabBatch` keyed by `custom_container_no`
# (a Data field carrying the Container *number*, not the Container doc name).
# To filter by HTY/VFY we need to consult the parent Container doc.
#
# IFNULL(transaction_type, 'VFY'): existing pre-HTY Containers have NULL
# because the field was added later. Per FRD's hard rule "VFY = unchanged
# starter behavior", NULL is treated as VFY so legacy data still appears
# under the VFY-filter view (and disappears under HTY-filter).


def get_container_nos_by_transaction_type(transaction_type):
    """Return the set of distinct Container.container_no values whose
    submitted parent Container has the given transaction_type. Used by
    the existing stock reports to post-aggregate-filter rows."""
    if not transaction_type:
        return None  # caller treats None as "no filter"
    rows = frappe.db.sql(
        """
        SELECT DISTINCT container_no
        FROM `tabContainer`
        WHERE docstatus = 1
          AND IFNULL(transaction_type, 'VFY') = %s
          AND IFNULL(container_no, '') != ''
        """,
        (transaction_type,),
    )
    return {r[0] for r in rows}


def filter_rows_by_transaction_type(rows, filters, container_field):
    """Apply an HTY transaction_type post-aggregate filter to a list of
    report rows. `container_field` is the row dict key carrying the
    Container.container_no value (varies per report — e.g. "Container
    Number" vs "Container No"). Blank/missing filter = pass-through."""
    if not rows:
        return rows
    tt = (filters or {}).get("transaction_type")
    if not tt:
        return rows
    allowed = get_container_nos_by_transaction_type(tt)
    if allowed is None:
        return rows
    return [r for r in rows if (r.get(container_field) or "") in allowed]


# ---------------------------------------------------------------------------
# MI1-I39 — HTY 4-step lot-based Delivery Note picker
# ---------------------------------------------------------------------------
# The HTY workflow (FRD §Delivery Note, "4-STEP LOT-BASED Delivery Note
# WORKFLOW") starts the user from Lot No, auto-displays Containers under that
# lot, accepts a multi-select, then materialises DN items from the selected
# Containers' Batch Items child rows. These three endpoints back the dialog.
# Each is read-only — they return data only, never mutate.


@frappe.whitelist()
def get_hty_lots(company=None):
    """List distinct lot_no across submitted Containers (optionally filtered
    by company). Ordered by most-recent posting_date first so today's lots
    appear at the top of the picker."""
    conditions = ["c.docstatus = 1", "IFNULL(c.lot_no, '') != ''"]
    params = {}
    if company:
        conditions.append("c.company = %(company)s")
        params["company"] = company
    where = " AND ".join(conditions)
    rows = frappe.db.sql(
        f"""
        SELECT c.lot_no, COUNT(*) AS container_count, MAX(c.posting_date) AS last_posting
        FROM `tabContainer` c
        WHERE {where}
        GROUP BY c.lot_no
        ORDER BY last_posting DESC, c.lot_no
        """,
        params,
        as_dict=True,
    )
    return rows


@frappe.whitelist()
def get_hty_containers_for_lot(lot_no, company=None):
    """For a given lot, return every submitted Container plus a per-row
    batch summary (count of batch rows + sum of net qty). The FRD says
    "if container physically unavailable, still allow selection from stock
    data" — so we DO NOT filter by remaining cones / stock balance here;
    the caller decides what to pick."""
    if not lot_no:
        return []
    conditions = ["c.docstatus = 1", "c.lot_no = %(lot_no)s"]
    params = {"lot_no": lot_no}
    if company:
        conditions.append("c.company = %(company)s")
        params["company"] = company
    where = " AND ".join(conditions)
    return frappe.db.sql(
        f"""
        SELECT c.name AS container, c.container_no, c.item AS item_code,
               c.lot_no, c.posting_date, c.company, c.supplier,
               c.lusture, c.glue, c.pulp, c.grade,
               c.total_batches, c.total_cone, c.total_net_weight,
               c.set_warehouse
        FROM `tabContainer` c
        WHERE {where}
        ORDER BY c.posting_date DESC, c.name
        """,
        params,
        as_dict=True,
    )


@frappe.whitelist()
def get_hty_batches_for_containers(container_names):
    """Return rows ready to drop into a Delivery Note items table.
    `container_names` may arrive as JSON (the client sends an Array via
    frappe.call) or as a list. Each row carries the fields the HTY DN row
    needs: item_code, qty, batch_no, custom_container_no, custom_lot_no,
    custom_cone, custom_sr_no, custom_gross_weight, custom_supplier_batch_no.
    The DN-form-side code is what actually appends rows — we just hand
    over the payload."""
    if isinstance(container_names, str):
        try:
            container_names = json.loads(container_names)
        except (ValueError, TypeError):
            container_names = [container_names]
    if not container_names:
        return []
    placeholders = ", ".join(["%s"] * len(container_names))
    rows = frappe.db.sql(
        f"""
        SELECT
            c.name AS container, c.container_no, c.lot_no,
            c.item AS item_code, c.set_warehouse,
            bi.batch_id, bi.qty AS net_weight, bi.cone,
            bi.supplier_batch_no, bi.idx,
            bi.custom_gross_weight, bi.custom_sr_no
        FROM `tabContainer` c
        INNER JOIN `tabBatch Items` bi ON bi.parent = c.name AND bi.parenttype = 'Container'
        WHERE c.name IN ({placeholders}) AND c.docstatus = 1
        ORDER BY c.name, bi.idx
        """,
        tuple(container_names),
        as_dict=True,
    )
    payload = []
    for r in rows:
        payload.append(
            {
                "item_code": r.item_code,
                "qty": flt(r.net_weight),
                "batch_no": r.batch_id,
                "warehouse": r.set_warehouse,
                "custom_container_no": r.container_no,
                "custom_lot_no": r.lot_no,
                "custom_cone": cint(r.cone),
                "custom_sr_no": r.custom_sr_no or "",
                "custom_gross_weight": flt(r.custom_gross_weight),
                "custom_supplier_batch_no": r.supplier_batch_no or "",
            }
        )
    return payload
