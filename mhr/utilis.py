import frappe
from frappe import _
from frappe.utils import cint, flt, getdate
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
  /* Single-page absolute-positioning layout (2026-06-23 third iter).
     Each PDF page is rendered as its OWN wkhtmltopdf invocation, so
     there's no multi-page CSS magic to misinterpret. Within that one
     page, 6 cells are hard-pinned at known (top, left) mm offsets on
     the body. wkhtmltopdf has nothing to "decide" about page breaks
     — we already split the work for it. */
  html, body { margin: 0; padding: 0; }
  /* Body height intentionally smaller than A4 (297mm). wkhtmltopdf adds
     ~12-15mm of mystery margin on top of body height, so setting body
     to 297mm caused the rendered content to spill onto a second PDF
     page (every invocation produced 1 real + 1 blank page). 280mm
     leaves enough headroom. */
  body {
    font-family: Arial, Helvetica, sans-serif; color: #000; font-size: 8.5pt;
    width: 210mm; height: 280mm;
    position: relative;
    margin: 0;
    overflow: hidden;
  }
  @page { size: A4 portrait; margin: 0; }

  /* 6 cells at fixed mm offsets. Cells 88mm tall × 100mm wide.
     3 × 88 = 264mm + 5mm top + 5mm bottom slack = 274mm < 280mm body. */
  .cell {
    position: absolute;
    width: 100mm; height: 88mm;
    padding: 3mm 4mm;
    box-sizing: border-box;
    overflow: hidden;
  }
  .cell.r1 { top: 3mm; }
  .cell.r2 { top: 94mm; }
  .cell.r3 { top: 185mm; }
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

    # Render each PDF page SEPARATELY and concatenate with pypdf.
    # Within one page we use absolute positioning on body (no
    # containing-block tricks) so wkhtmltopdf has no degrees of freedom
    # to insert a break — we're feeding it exactly one A4 page worth
    # of fixed-position content per invocation.
    from frappe.utils.pdf import get_pdf
    from pypdf import PdfReader, PdfWriter
    import io

    # Reading-order positions (TL → TR → ML → MR → BL → BR).
    positions = [
        ("r1", "c1"), ("r1", "c2"),
        ("r2", "c1"), ("r2", "c2"),
        ("r3", "c1"), ("r3", "c2"),
    ]

    writer = PdfWriter()
    for i in range(0, len(labels), 6):
        chunk = labels[i:i + 6]
        cells_html = "".join(
            f'<div class="cell {row} {col}">{chunk[k]}</div>'
            for k, (row, col) in enumerate(positions)
        )
        page_html = (
            "<!doctype html><html><head><meta charset='utf-8'>"
            + HTY_6UP_STYLE
            + "</head><body>"
            + cells_html
            + "</body></html>"
        )
        page_pdf = get_pdf(page_html, options={
            "page-size": "A4",
            "margin-top": "0",
            "margin-bottom": "0",
            "margin-left": "0",
            "margin-right": "0",
        })
        reader = PdfReader(io.BytesIO(page_pdf))
        # Per-invocation HTML carries exactly 6 cells of content — take
        # only the FIRST page. wkhtmltopdf often emits a trailing blank
        # page from absolute-positioned body content (cause unknown; the
        # body has overflow:hidden + height < A4). Discarding subsequent
        # pages gives us the clean N-page output the user expects.
        if reader.pages:
            writer.add_page(reader.pages[0])

    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


@frappe.whitelist()
def make_receive_from_subcontractor(source_name):
    """MI1-I50: build a draft "Job Work Received" Stock Entry that receives
    material back from a subcontractor against a submitted 'Send to
    Subcontractor' entry.

    - Source must be Submitted + purpose 'Send to Subcontractor'.
    - Per item, pending = qty - custom_received_qty. Skip fully-received.
    - Sent rows: source = the subcontractor warehouse, batch auto-fetched
      from the Send, target left blank (the header target defaults to where
      the material was sent from — MI1-I103).
    - Received Container / Lot header fields default to the Send's values
      and stay editable (Raj 2026-09-03).
    - Item custom fields (cone, supplier batch, gross weight, ...) carry over.
    - Links back to the original via custom_original_send_entry.
    - Purpose is settled per document in set_receive_purpose; new / finished
      rows the user adds get their batch in create_receive_batches.

    Returns the new draft's name; caller (JS) navigates to it.
    """
    source = frappe.get_doc("Stock Entry", source_name)
    if source.docstatus != 1:
        frappe.throw(_("Source Stock Entry must be Submitted."))
    if source.purpose != "Send to Subcontractor":
        frappe.throw(_("Source Stock Entry purpose must be 'Send to Subcontractor'."))

    receipt = frappe.new_doc("Stock Entry")
    # MI1-I50 (2026-06-23 follow-up): Raj wants the receive entry typed
    # as 'Job Work Received' (a custom Stock Entry Type seeded by
    # mhr.patches.v1_0.seed_job_work_received_stock_entry_type with
    # purpose='Material Transfer'). Material Transfer purpose keeps
    # both source and target warehouses required, matching the
    # warehouse-pair semantics Raj expects.
    receipt.stock_entry_type = "Job Work Received"
    receipt.purpose = "Material Transfer"
    receipt.company = source.company
    receipt.posting_date = frappe.utils.nowdate()
    receipt.posting_time = frappe.utils.nowtime()
    receipt.set_posting_time = 1
    receipt.set("custom_original_send_entry", source.name)

    # MI1-I50 (Raj 2026-09-03): the subcontractor may return material in a
    # different container / lot than the one sent. Default both editable
    # fields to the Send's values; the user overrides at receiving time.
    # These two (plus the row's Supplier Batch No) name every NEW batch
    # created on submit — see create_receive_batches.
    if receipt.meta.has_field("custom_received_container_no"):
        receipt.custom_received_container_no = source.get("custom_container_number") or ""
    if receipt.meta.has_field("custom_received_lot_no"):
        receipt.custom_received_lot_no = source.get("custom_lot_no") or ""

    # MI1-I103: default the header target to where the material was sent FROM.
    # The rows are still left blank on purpose (see below), but a blank header
    # too meant the draft could not even be SAVED —
    # StockEntry.validate_warehouse runs from validate() and throws "Target
    # warehouse is mandatory for row 1" only when the row AND the header are
    # empty; given a header it fills the rows itself. The user can still change
    # it before submit, and changing it propagates to the rows.
    receipt.to_warehouse = source.from_warehouse or next(
        (item.s_warehouse for item in source.items if item.s_warehouse), None
    )

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
            # MI1-I50 (Raj 2026-09-03): a SENT item comes back under the
            # batch it left with — auto-fetched, never typed. Only rows the
            # user ADDS for new / finished material have no batch, and those
            # get one generated on submit (create_receive_batches). This
            # replaces the 2026-07-17 rule that blanked every row.
            "batch_no": src_item.batch_no,
            "serial_no": src_item.serial_no,
            "use_serial_batch_fields": 1,
            # MI1-I50 reopen (Raj 2026-07-17): warehouses flip so the
            # material comes BACK from the subcontractor:
            #   Source WH = source Send's Target WH (subcontractor)
            #   Target WH = BLANK — user picks manually before submit
            # Prior iteration (2026-06-23) kept both warehouses the same
            # as the Send entry; today's spec overrides that.
            "s_warehouse": src_item.t_warehouse,
            "t_warehouse": "",
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

    # ERPNext owns the validation (MI1-I103). The rows' targets are blank on
    # purpose, but with the header target defaulted above
    # StockEntry.validate_warehouse fills them itself, so the draft inserts
    # under the full validation chain — including set_receive_purpose
    # (before_validate) and validate_subcontract_receipt. The 2026-07-17
    # skip-validation shortcut is gone: it also skipped every mhr hook.
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
    """(item_code, container_no, lot_no, supplier_batch_no) — the join key
    between Receive and Source rows.

    MI1-I50 reopen (Raj 2026-07-17): the Receive entry now carries a NEWLY
    generated batch (see `create_receive_batches`) so the old (item_code,
    batch_no) key stopped matching. The container / lot / supplier-batch
    triplet is stable across both sides — it's exactly the trio Raj
    identifies each pending job-work row by — so we match on that instead.

    Source-built receipts mirror source rows 1:1, but the user can edit qty
    or add rows. We aggregate by this key on both sides so multi-row source
    + edited receipt still reconciles correctly."""
    return (
        item.item_code,
        (item.get("custom_container_no") or ""),
        (item.get("custom_lot_no") or ""),
        (item.get("custom_supplier_batch_no") or ""),
    )


def _receive_batch_id(doc, item):
    """MI1-I50 (Raj 2026-09-03): the auto-generated Batch ID for a NEW /
    finished item on a Receive-from-Subcontractor entry:

        Received Container No - Received Lot No - Supplier Batch No

    e.g. `MC-JC-2222 + 13042026 + 6086` → `MC-JC-2222-13042026-6086`.

    Container and lot come from the entry HEADER (the two editable
    "Received …" fields — the subcontractor may return material in a
    different container / lot than the one sent); the supplier batch
    number comes from the row. Stock Entry Detail has no container / lot
    columns of its own, which is why the 2026-07-17 row-based derivation
    never resolved.

    Returns None when any of the three is missing — the caller treats
    that as a hard validation error so an unbatchable row cannot submit."""
    container = (doc.get("custom_received_container_no") or "").strip()
    lot = (doc.get("custom_received_lot_no") or "").strip()
    supplier_batch = (item.get("custom_supplier_batch_no") or "").strip()
    if not (container and lot and supplier_batch):
        return None
    return f"{container}-{lot}-{supplier_batch}"


# Header spec fields copied onto every Batch created at receiving, and the
# Batch column each one lands in. Raj (2026-09-03): "container-related
# information should be editable at Job Work Received, like Container
# Inward, and saved against the Received Container Number" — the Batch is
# what every downstream picker (Delivery Note popups, Fetch Batches, the
# stock sheets) reads, so the spec lives there, keyed by the received
# container / lot.
_RECEIVE_BATCH_SPEC_FIELDS = (
    ("custom_glue", "custom_glue"),
    ("custom_pulp", "custom_pulp"),
    ("custom_lusture", "custom_lusture"),
    ("custom_grade", "custom_grade"),
    ("custom_fsc", "custom_fsc"),
    ("custom_merge_no", "custom_merge_no"),
    ("custom_notes", "custom_notes"),
    ("custom_cross_section", "cross_section"),
)


def set_receive_purpose(doc, method=None):
    """MI1-I50 (Raj 2026-09-03): before_validate hook — decide the Stock
    Entry purpose of a Receive-from-Subcontractor entry from its rows.

    A receipt can now carry two kinds of row:
      * SENT material coming back (source = subcontractor warehouse,
        batch auto-fetched) — a transfer if the user gives it a target,
        otherwise consumed at the subcontractor.
      * NEW / finished material (target warehouse only, no source) that
        gets a fresh batch on submit.

    ERPNext's "Material Transfer" demands both warehouses on every row, so
    it cannot carry a finished row; "Repack" allows source-only and
    target-only rows side by side but throws "There must be atleast 1
    Finished Good" when no row is target-only. So: Repack when any row is
    target-only, Material Transfer otherwise. `purpose` is a per-document
    field — StockEntry.set_purpose_for_stock_entry() only fills it when
    empty — so the "Job Work Received" type keeps its stored purpose and
    each document settles its own. Runs before StockEntry.validate(), which
    is what reads purpose for the warehouse and finished-goods checks.

    Fast no-op for every Stock Entry that is not a receipt.
    """
    if not _subcontract_source_name(doc):
        return
    has_finished_row = any(
        row.get("t_warehouse") and not row.get("s_warehouse") for row in (doc.get("items") or [])
    )
    doc.purpose = "Repack" if has_finished_row else "Material Transfer"


@frappe.whitelist()
def create_receive_batches(doc, method=None):
    """MI1-I50 (Raj 2026-09-03): before_submit hook on Stock Entry.
    For every row on a Receive-from-Subcontractor entry with an empty
    batch_no — i.e. NEW / finished material, since sent rows arrive with
    their original batch — generate a Batch named
    `received_container-received_lot-supplier_batch_no`, copy the header's
    container information onto it, and assign it to the row.

    Fast-no-op on every Stock Entry that isn't a Receive entry
    (`custom_original_send_entry` empty). Runs on before_submit, not
    validate, so re-saving a draft with different container/lot/supplier-
    batch values doesn't lock in the FIRST batch derivation.

    Validation:
      * All three fields (container, lot, supplier batch) must be set on
        every row that needs a batch — otherwise we can't derive the ID.
      * If the derived Batch already exists → hard block (Raj's spec:
        "prevent duplicate creation and display an appropriate
        validation message"). A duplicate ID usually means someone
        already submitted the same receive earlier.
    """
    source_name = _subcontract_source_name(doc)
    if not source_name:
        return

    batch_meta = frappe.get_meta("Batch")
    for row in doc.get("items") or []:
        if row.batch_no:
            # Sent material coming back (batch auto-fetched from the Send)
            # or a batch the user chose deliberately — leave it alone.
            continue
        batch_id = _receive_batch_id(doc, row)
        if not batch_id:
            frappe.throw(_(
                "Cannot generate Batch for item <b>{0}</b>: Received Container "
                "Number and Received Lot No (on the entry) and Supplier Batch "
                "No (on the row) must all be set before submitting a "
                "Receive-from-Subcontractor entry."
            ).format(row.item_code))
        if frappe.db.exists("Batch", batch_id):
            frappe.throw(_(
                "Batch <b>{0}</b> already exists — a Receive entry for the "
                "same Received Container / Received Lot / Supplier Batch "
                "combination was likely already submitted. Delete the "
                "duplicate row or amend the existing Receive."
            ).format(batch_id))
        batch = frappe.new_doc("Batch")
        batch.batch_id = batch_id
        batch.item = row.item_code
        batch.batch_qty = flt(row.qty)
        # Identity: the RECEIVED container / lot (header) + the row's
        # supplier batch — the trio every Delivery Note popup, Fetch Batches
        # and stock sheet keys on.
        batch.custom_container_no = doc.get("custom_received_container_no") or ""
        batch.custom_lot_no = doc.get("custom_received_lot_no") or ""
        batch.custom_supplier_batch_no = row.get("custom_supplier_batch_no") or ""
        batch.custom_cone = cint(row.get("custom_cone") or doc.get("custom_se_cone") or 0)
        # Mode, so the HTY / VFY-scoped batch dropdowns (MI1-I76) see it.
        if batch_meta.has_field("custom_transaction_type"):
            batch.custom_transaction_type = doc.get("transaction_type") or ""
        # Inward date drives the Stock Sheet Aging column (MI1-I94).
        if batch_meta.has_field("manufacturing_date") and doc.get("posting_date"):
            batch.manufacturing_date = doc.get("posting_date")
        # Container information as entered at receiving (Raj 2026-09-03 §5).
        for src_field, batch_field in _RECEIVE_BATCH_SPEC_FIELDS:
            if batch_meta.has_field(batch_field) and doc.get(src_field) not in (None, ""):
                batch.set(batch_field, doc.get(src_field))
        batch.insert(ignore_permissions=True)
        row.batch_no = batch.name
        # Force use_serial_batch_fields so ERPNext writes the batch onto
        # the SLE row directly (rather than requiring a SBB, which would
        # need a separate submission cycle we're inside of).
        row.use_serial_batch_fields = 1


@frappe.whitelist()
def validate_subcontract_receipt(doc, method=None):
    """MI1-I50 P3: on a Receive entry's validate, refuse over-receipts beyond
    pending * (1 + custom_overreceipt_tolerance_pct / 100) per
    (item, container_no, lot_no, supplier_batch_no).

    MI1-I50 reopen (Raj 2026-07-17): the join key switched from
    (item, batch) to the container/lot/supplier-batch triplet because the
    Receive entry now carries a NEWLY generated batch (see
    `create_receive_batches`), so `(item, batch)` no longer maps onto
    the source rows.

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

    # Sum incoming receipt per key — for rows that RETURN sent material.
    # MI1-I50 (Raj 2026-09-03): a receipt may also carry new / finished
    # items that were never sent. Their key matches no source row, so there
    # is no pending qty to check against; they must not be blocked as
    # "over-receipts". _apply_receipt_delta skips them for the same reason.
    incoming = {}
    for r in doc.items:
        key = _subcontract_match_key(r)
        if key not in pending:
            continue
        incoming[key] = incoming.get(key, 0.0) + flt(r.qty)

    for key, inc in incoming.items():
        pend = pending.get(key, 0.0)
        max_allowed = pend * (1.0 + tolerance_pct / 100.0)
        if inc > max_allowed + _SUBCONTRACT_QTY_EPSILON:
            # MI1-I50 reopen (2026-07-17): key is now
            # (item_code, container_no, lot_no, supplier_batch_no).
            item_code, container_no, lot_no, supplier_batch_no = key
            row_label = (
                f"container {container_no or '—'} / lot {lot_no or '—'} / "
                f"supplier batch {supplier_batch_no or '—'}"
            )
            frappe.throw(_(
                "Over-receipt blocked for item <b>{0}</b> ({1}): "
                "pending {2}, tolerance {3}%, but this entry is taking {4}. "
                "Either reduce the qty on this row or raise "
                "<b>Over-Receipt Tolerance</b> on the source Send entry."
            ).format(item_code, row_label,
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


def resolve_spec_value(spec_name):
    """MI1-I107: the display value of an Item Specification, exactly.

    Container stores its specs as Links, so a Batch inherits the DOCNAME —
    'Colour-Black', 'Grade-AA'. Reports have always shown the tail by calling
    strip_prefix() at read time. This resolves it properly instead, because
    MI1-I107 writes the value into the Batch permanently and a lossy split
    would be baked into the data.

    strip_prefix() splits on the LAST hyphen, which is wrong for real records
    on this site:

        'Grade-Off-Grade'       -> 'Grade'      (should be 'Off-Grade')
        'Grade: .'              -> 'Grade: .'   (colon, so nothing is stripped)
        'Lusture-Special Silky' -> 'Special Silky', but that record's actual
                                   value is ' SPECIAL SILKY' — different case
                                   and a leading space

    Item Specification carries the answer in its own `value` field, so read
    that, and fall back to strip_prefix() only when no such record exists (a
    free-typed value, or a spec deleted after the Batch was created).

    Cached per request: create_batches() calls this four times per batch, and
    a container can carry hundreds.
    """
    if not spec_name:
        return ""

    # frappe.flags is a plain _dict living on frappe.local, so this is scoped
    # to the request / job and cannot go stale between them. NOT
    # frappe.local.__dict__ — frappe.local is a werkzeug-style Local whose
    # __getattribute__ raises AttributeError for __dict__ (frappe/utils/local.py),
    # which is why frappe's own code reaches for it with getattr().
    cache = frappe.flags.setdefault("_mhr_spec_value_cache", {})
    key = str(spec_name)
    if key in cache:
        return cache[key]

    value = frappe.db.get_value("Item Specification", key, "value")
    # `value` can legitimately be '' — only fall back when the record is absent.
    resolved = strip_prefix(key) if value is None else value
    cache[key] = resolved
    return resolved


def grade_filter_value(grade):
    """MI1-I107: a Batch.custom_grade filter that matches both storage forms.

    HTY Batches now hold the plain grade ('AA'); the 445k VFY Batches still
    hold the Item Specification docname ('Grade-A EVEN'). A Delivery Note or
    Stock Entry header can be carrying either — a document created before the
    backfill still has the prefixed value, and its Fetch Batches would find
    nothing against an exact match.

    Returns an `in` clause covering both forms, so the lookup works whichever
    side is which. Not a loosening in practice: the two forms are the same
    grade, and no Batch holds one grade's plain value as another's prefixed
    name.
    """
    if not grade:
        return None

    raw = str(grade)
    plain = resolve_spec_value(raw)
    variants = [v for v in dict.fromkeys([raw, plain]) if v]
    return variants[0] if len(variants) == 1 else ["in", variants]


def hty_aware_specs(container):
    """The glue / lusture / pulp a Batch should inherit from `container`.

    HTY Containers leave glue / lusture / pulp EMPTY and capture the same three
    concepts under product / colour / type, so those are folded back into the
    canonical columns. Both create_batches() paths already did this inline;
    MI1-I107 lifted it here because heal_orphan_batch_masters did NOT, and was
    therefore healing HTY Batches with blank specs.

    `container` may be a Container Document or a plain row dict.
    """
    if (container.get("transaction_type") or "VFY") == "HTY":
        return {
            "glue": container.get("product"),
            "lusture": container.get("colour"),
            "pulp": container.get("type"),
        }
    return {
        "glue": container.get("glue"),
        "lusture": container.get("lusture"),
        "pulp": container.get("pulp"),
    }


def apply_hty_spec_values(batch_doc, container):
    """MI1-I107: fill a Batch's HTY spec fields with plain values.

    HTY Containers capture their specs under colour / product / type, and
    create_batches() folds those into the canonical custom_lusture /
    custom_glue / custom_pulp columns. Those columns keep the raw Item
    Specification docname ('Colour-Black') because live code matches on them —
    mhr.utilis.get_delivery_note_batch and mhr.note.fetch_batches both filter Batch by the
    exact string the Delivery Note header holds.

    So the plain values go into their OWN fields, which the Batch form shows
    only in HTY mode:

        custom_colour   <- Container.colour     'Colour-Black'  -> 'Black'
        custom_product  <- Container.product    'Product-Chips' -> 'Chips'
        custom_type     <- Container.type       'Type-Bag'      -> 'Bag'

    custom_grade is the exception, per the MI1-I107 spec: no new field and no
    visibility condition, the existing one simply stops carrying the prefix.
    Only for HTY — the 445k VFY Batches keep 'Grade-A EVEN' exactly as they
    are, so nothing VFY reads or filters changes.

    `container` may be a Container Document or a plain row dict; both support
    .get(). No-op for VFY.
    """
    if (container.get("transaction_type") or "VFY") != "HTY":
        return

    batch_doc.custom_colour = resolve_spec_value(container.get("colour"))
    batch_doc.custom_product = resolve_spec_value(container.get("product"))
    batch_doc.custom_type = resolve_spec_value(container.get("type"))
    batch_doc.custom_grade = resolve_spec_value(container.get("grade"))


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

    # MI1-I89 (Raj 2026-07-20): cone filter ALWAYS applies — supersedes
    # the MI1-I78 P5 leniency below. Raj's expected behaviour: if the
    # header custom_batch is an 8-cone batch (which sets custom_cone=8),
    # a supplier_batch_no lookup must NOT return a 12-cone batch. To
    # switch to a 12-cone batch the user picks a new custom_batch first,
    # which updates header cone → matching SBNs then resolve.
    if cone and is_return is False:
        filters["custom_cone"] = cone

    # MI1-I78 P5 (Raj 2026-07-13): when supplier_batch_no is supplied it's
    # specific enough (together with the always-on cone filter above) to
    # uniquely identify a batch within a container/lot. Skip the OTHER
    # spec filters (glue/pulp/lusture/grade/fsc/denier) — otherwise a
    # mismatched header spec (from a previously-picked batch) blocks the
    # lookup entirely and the user sees "not fetching".
    if not supplier_batch_no:
        if glue:
            filters["custom_glue"] = glue
        if pulp:
            filters["custom_pulp"] = pulp
        if fsc:
            filters["custom_fsc"] = fsc
        if lusture:
            filters["custom_lusture"] = lusture
        if grade:
            # MI1-I107: HTY Batches hold the plain grade, VFY the prefixed
            # docname; the header may carry either.
            filters["custom_grade"] = grade_filter_value(grade)
        if denier and is_return is False:
            filters["item_name"] = denier

    # Check if at least one filter is applied
    if filters:
        if frappe.db.exists("Batch", filters):
            item = frappe.get_doc("Batch", filters)

            # MI1-I78 P7 (Raj 2026-07-13): also resolve the warehouse
            # where this batch physically has stock. The SE/DN's default
            # source warehouse is company-driven (Meher Creations →
            # Vadod - MC), but a batch inwarded under Meher International
            # lives in Vadod - MI. Without the correct s_warehouse the
            # newly-appended items row triggers "negative stock" on
            # submit. Prefer the warehouse with the largest positive
            # balance for this batch.
            resolved_warehouse = _resolve_batch_warehouse(item.name)

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
                "warehouse": resolved_warehouse,
            }


def _resolve_batch_warehouse(batch_no):
    """MI1-I78 P7 helper: return the warehouse holding the largest
    positive balance for the given batch. Reads from Serial and Batch
    Bundle since batchwise valuation batches don't index on
    tabStock Ledger Entry.batch_no.

    Returns None if the batch isn't inwarded anywhere yet.
    """
    if not batch_no:
        return None
    rows = frappe.db.sql(
        """
        SELECT sbb.warehouse, SUM(sbe.qty) AS balance
        FROM `tabSerial and Batch Bundle` sbb
        INNER JOIN `tabSerial and Batch Entry` sbe ON sbe.parent = sbb.name
        WHERE sbe.batch_no = %s
          AND sbb.docstatus = 1
          AND sbb.is_cancelled = 0
          AND sbb.type_of_transaction IN ('Inward', 'Outward')
        GROUP BY sbb.warehouse
        HAVING balance > 0
        ORDER BY balance DESC
        LIMIT 1
        """,
        (batch_no,),
        as_dict=True,
    )
    return rows[0]["warehouse"] if rows else None


@frappe.whitelist()
def get_container_batches_with_stock(container_no):
    """MI1-I71 reopen (Raj 2026-07-13): return batches for a container
    that still have positive available stock.

    Root cause of the "Batch has negative stock" error on DN submit
    when the user picks a batch from the HTY / VFY 'Select Batch'
    popup: the popup lists ALL batches under the container, including
    ones that were already fully delivered. Users pick a zero-stock
    batch → append → submit → ERPNext throws BatchNegativeStockError.

    This helper joins Batch → Serial and Batch Bundle to compute the
    net balance per (batch, warehouse) pair, and returns only rows
    where the sum > 0. The client uses this in place of
    frappe.client.get_list for the popup fetch so zero-stock batches
    never appear.

    Returned dict keys mirror what get_all_batches in the HTY & VFY
    Client Script already reads:
        name, custom_lot_no, custom_cone, custom_glue, custom_pulp,
        custom_lusture, custom_grade, custom_fsc, item, item_name,
        manufacturing_date, batch_qty, stock_uom, custom_supplier_batch_no,
        custom_container_no, custom_warehouse
    plus:
        available_qty, warehouse (the warehouse holding the balance)
    """
    if not container_no:
        return []

    # Aggregate net balance per (batch, warehouse) from SBB entries.
    # HAVING balance > 0 drops fully-delivered / never-inwarded rows.
    balances = frappe.db.sql(
        """
        SELECT sbe.batch_no, sbb.warehouse, SUM(sbe.qty) AS balance
        FROM `tabSerial and Batch Bundle` sbb
        INNER JOIN `tabSerial and Batch Entry` sbe ON sbe.parent = sbb.name
        INNER JOIN `tabBatch` b ON b.name = sbe.batch_no
        WHERE b.custom_container_no = %s
          AND sbb.docstatus = 1
          AND sbb.is_cancelled = 0
          AND sbb.type_of_transaction IN ('Inward', 'Outward')
        GROUP BY sbe.batch_no, sbb.warehouse
        HAVING balance > 0
        """,
        (container_no,),
        as_dict=True,
    )
    if not balances:
        return []

    # One batch may have positive balance in multiple warehouses; the
    # popup renders one row per batch, so collapse to the warehouse
    # holding the largest positive balance.
    by_batch = {}
    for row in balances:
        cur = by_batch.get(row["batch_no"])
        if cur is None or row["balance"] > cur["balance"]:
            by_batch[row["batch_no"]] = row

    batch_names = list(by_batch.keys())
    if not batch_names:
        return []

    # Fetch the batch metadata in one shot.
    batches = frappe.get_all(
        "Batch",
        filters={"name": ["in", batch_names]},
        fields=[
            "name", "custom_lot_no", "custom_cone", "custom_glue",
            "custom_pulp", "custom_lusture", "custom_grade", "custom_fsc",
            "item", "item_name", "manufacturing_date", "batch_qty",
            "stock_uom", "custom_supplier_batch_no",
            "custom_container_no", "custom_warehouse",
        ],
        order_by="name asc",
    )

    # Attach the resolved warehouse + available qty from the SBB
    # aggregate, then return.
    for b in batches:
        entry = by_batch.get(b["name"])
        if entry:
            b["available_qty"] = entry["balance"]
            b["warehouse"] = entry["warehouse"]
            # MI1-I71 (Raj 2026-07-17): the HTY popup renders
            # `batch_qty` in the 'Batch Qty' column, and the Select
            # handler writes it into the DN row's qty. Overwrite the
            # stale Batch master value with the current SBB balance
            # so both the display and the row qty match reality.
            b["batch_qty"] = entry["balance"]
    return batches


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


# MI1-I103 — update_batch_warehouse_on_stock_entry and its on_cancel twin were
# REMOVED here. They wrote the Stock Entry's target warehouse into four inward
# attributes that do not track live stock: Container.set_warehouse (the
# "Accepted Warehouse" the inward Purchase Receipt and every Serial and Batch
# Bundle post to), Container.warehouse, Batch.custom_warehouse and
# Batch Items.warehouse (free-text "Location" notes). 87 submitted Containers
# were left holding a subcontractor's warehouse as their accepted one.
#
# Live stock location comes from Serial and Batch Bundle instead — see
# get_all_batches_with_stock above and _clamp_batch_qty_to_available in
# mhr/note.py. mhr/patches/v1_0/heal_container_accepted_warehouse.py repairs
# the damaged containers from their Purchase Receipt.


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


def set_batch_transaction_type_from_container(doc, method=None):
    """Auto-fill Batch.custom_transaction_type from the linked Container.

    Custom Field `Batch-custom_transaction_type` is a Link to Transaction
    Type, but `Batch.custom_container_no` is just a varchar holding the
    user-facing container_no value (e.g. 'MCJC-1614') — not a Link to a
    specific Container.name. fetch_from can't bridge that, so this
    server hook does the lookup explicitly:
      Batch.custom_container_no  -> Container.container_no
      first matching Container's transaction_type  -> Batch.custom_transaction_type

    Skips:
      - Batches without custom_container_no (can't resolve)
      - Batches whose custom_transaction_type is already set (manual
        override stays intact)
    """
    if doc.get("custom_transaction_type"):
        return
    container_no = doc.get("custom_container_no")
    if not container_no:
        return
    tt = frappe.db.get_value(
        "Container",
        {"container_no": container_no},
        "transaction_type",
    )
    if tt:
        doc.custom_transaction_type = tt


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
            # MI1-I107: HTY gets Colour / Product / Type as plain values, and
            # its custom_grade loses the 'Grade-' prefix. VFY untouched.
            apply_hty_spec_values(batch_doc, container_doc)
            batch_doc.custom_fsc = container_doc.fsc
            batch_doc.custom_lot_no = container_doc.lot_no
            # MI1-I63 (reopen, 2026-06-29): propagate Gross Weight from
            # Batch Items row onto Batch master — same reason as the
            # duplicate copy in Container.create_batches. Without this,
            # DN Item's custom_gross_weight fetch_from and the HTY 6-up
            # barcode both read 0.
            batch_doc.custom_gross_weight = flt(batch.get("custom_gross_weight") or 0)
            # MI1 (Raj 2026-07-10) — mirror the batch_qty +
            # manufacturing_date copy in Container.create_batches so
            # the two paths stay in sync. HTY Select-Batch popup was
            # showing '-' for both columns before this.
            batch_doc.batch_qty = flt(batch.get("qty") or 0)
            if container_doc.posting_date:
                batch_doc.manufacturing_date = getdate(container_doc.posting_date)
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


def fetch_notes_from_container(doc, method=None):
    """MI1-I83 (Raj 2026-07-18): auto-populate the DN's `custom_notes`
    field from the linked Container Inward's `notes` when creating a
    VFY Delivery Note.

    Why not a `fetch_from` on the custom field? Because DN's
    `custom_container_no` is a Data field (not a Link), so Frappe's
    `fetch_from` doesn't apply — we resolve the Container manually via
    (container_no, transaction_type='VFY') lookup.

    MI1-I101 (2026-08-17): extended to HTY. The original was VFY-only per
    the MI1-I83 spec; HTY Delivery Notes now need the same behaviour, and
    the Container is resolved against the DN's OWN transaction_type so the
    two modes never read each other's inwards.

    Rules:
      * Scope: VFY and HTY. Any other value is ignored.
      * If `custom_notes` already has a value, preserve it (the user
        may have typed an override, OR the existing
        `custom_batch.custom_notes` fetch_from may have populated it
        from a Batch that also had notes — either way, first non-empty
        value wins). The on-change client handler is what REPLACES the
        value when the user actively switches container.
      * If Container.notes is empty, do nothing (leave DN blank).
      * Existing fetches of other Container details are unaffected.
    """
    tt = (doc.get("transaction_type") or "VFY").upper()
    if tt not in ("VFY", "HTY"):
        return
    if doc.get("custom_notes"):
        return
    notes = resolve_container_notes(doc.get("custom_container_no"), tt)
    if notes:
        doc.custom_notes = notes


def resolve_container_notes(container_no, transaction_type):
    """Container Inward `notes` for a (container_no, transaction_type) pair.

    MI1-I101: `container_no` is NOT unique — one number maps to many Container
    documents (e.g. MZB-32 has six). In practice siblings agree on `notes`, but
    the previous `frappe.db.get_value` let the database pick an arbitrary row,
    and it did not exclude drafts. Resolve deterministically instead: submitted
    Containers only, most recent inward wins.
    """
    if not container_no:
        return None
    tt = (transaction_type or "VFY").upper()
    rows = frappe.get_all(
        "Container",
        filters={
            "container_no": container_no,
            "transaction_type": tt,
            "docstatus": 1,
        },
        fields=["notes"],
        order_by="posting_date desc, modified desc",
        limit=1,
    )
    return (rows[0].get("notes") or None) if rows else None


def ensure_total_qty(doc, method=None):
    """Fill Total Quantity when a save arrives with it still 0.

    Rows appended with frm.add_child() fire no grid event, so neither items_add
    nor ERPNext's calculate_taxes_and_totals runs and total_qty can reach save
    untouched. Guarded on falsy rather than recomputed unconditionally, so a
    value ERPNext already settled — including a deliberate 0 on an empty
    document — is left alone. Registered on both Delivery Note (via
    calculate_delivery_note_totals) and Sales Order.
    """
    if not flt(doc.total_qty) and doc.items:
        doc.total_qty = sum(flt(item.qty) for item in doc.items)


def calculate_delivery_note_totals(doc, method=None):
    total_cone = 0
    for item in doc.items:
        total_cone += cint(item.custom_cone or 0)
    doc.custom_total_cone = total_cone
    doc.custom_item_length = len(doc.items)

    ensure_total_qty(doc)

    set_header_container_info_from_items(doc)
    # MI1-I63 (2026-06-23): backfill DN Item gross weight from the linked
    # Batch master. The Custom Field already has fetch_from set so the
    # form auto-fills on batch_no change — but programmatic DN creates
    # (e.g. mapper from Sales Order, bulk imports) bypass the form
    # fetch. Doing it here on validate guarantees the field is populated
    # before submit no matter how the row was created. fetch_if_empty
    # semantics: only fill rows whose custom_gross_weight is currently
    # 0 / unset, so any manual override stays intact.
    backfill_dn_item_gross_weight(doc)


def backfill_dn_item_gross_weight(doc):
    """MI1-I63: copy Batch.custom_gross_weight onto Delivery Note Item rows.

    Skips:
      - rows without a batch_no (can't resolve)
      - rows whose custom_gross_weight is already > 0 (manual override)
    """
    batch_nos = list({
        i.batch_no for i in (doc.get("items") or [])
        if i.get("batch_no") and not flt(i.get("custom_gross_weight") or 0) > 0
    })
    if not batch_nos:
        return
    rows = frappe.get_all(
        "Batch",
        filters={"name": ["in", batch_nos]},
        fields=["name", "custom_gross_weight"],
    )
    by_batch = {r.name: flt(r.custom_gross_weight or 0) for r in rows}
    for item in doc.items:
        if not item.batch_no:
            continue
        if flt(item.get("custom_gross_weight") or 0) > 0:
            continue
        gw = by_batch.get(item.batch_no, 0)
        if gw:
            item.custom_gross_weight = gw


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


# ----------------------------------------------------------------------
# Cancel a large Delivery Note in the background.
#
# Same failure as MI1-I26's Stock Entry submit, on the other end of the
# document's life. MC-CH-DL-DN00035 carries 1000 item rows, 1000 Stock
# Ledger Entries and 1000 Serial and Batch Bundles; cancelling means
# cancelling every one of those bundles and reversing every ledger entry.
# That runs well past the gunicorn request limit, so the browser gets
# "Request Timed Out" while the worker is still mid-cancel — and because
# nothing committed, the note is left submitted with no sign of the
# attempt. Same endpoint shape as MI1-I26: return immediately, do the work
# on a worker, tell the form over realtime when it lands.
#
# TEMPORARY — delete when it stops earning its place: when ERPNext's cancel
# is fast enough at this row count, or Meher stops building notes this large.
# Both functions below go together with the Client Script record
# "MI1 — Delivery Note Cancel in Background" and
# mhr/tests/test_dn_cancel_in_background.py.
# ----------------------------------------------------------------------


@frappe.whitelist()
def cancel_delivery_note_in_background(name):
    """Queue the Delivery Note cancel on a background worker.

    Returns immediately so the HTTP layer doesn't time out.
    """
    if not name:
        frappe.throw(_("Delivery Note name is required."))

    doc = frappe.get_doc("Delivery Note", name)
    if doc.docstatus != 1:
        frappe.throw(
            _("Delivery Note {0} is not submitted. Current docstatus={1}.").format(
                name, doc.docstatus
            )
        )

    # The worker runs outside the request, so the Cancel permission the Desk
    # would normally enforce has to be checked here, before enqueueing.
    doc.check_permission("cancel")

    # deduplicate: a second click while the first job is queued or running
    # would otherwise start a competing cancel on the same document.
    frappe.enqueue(
        method="mhr.utilis._cancel_delivery_note_worker",
        queue="long",
        timeout=3600,
        job_id=f"mhr-cancel-delivery-note-{name}",
        deduplicate=True,
        name=name,
        notify_user=frappe.session.user,
    )
    return {"queued": True, "name": name}


def _cancel_delivery_note_worker(name, notify_user):
    """Worker: load + cancel. Publishes realtime when done."""
    ok = False
    error = ""
    try:
        doc = frappe.get_doc("Delivery Note", name)
        if doc.docstatus == 1:
            doc.cancel()
            frappe.db.commit()
        ok = True
    except Exception as exc:
        frappe.db.rollback()
        error = str(exc)
        frappe.log_error(
            frappe.get_traceback(),
            f"mhr cancel_delivery_note_worker failed for {name}",
        )
    frappe.publish_realtime(
        event="mhr_delivery_note_cancelled",
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


def enforce_role_scoped_transaction_type(filters):
    """MI1-I61 (2026-06-27) + MI1-I80 (Raj 2026-07-15): if the user has a
    Frappe User Permission with allow='Transaction Type' and a single
    for_value ('HTY' or 'VFY'), force filters.transaction_type to that
    value. This runs at the top of every mhr report's execute() — it
    OVERWRITES the user-supplied transaction_type value, so a
    permission-restricted user cannot see the other mode's data by
    tweaking the filter.

    Behaviour per Raj's spec:
      * Case 1 — permission for 'HTY' only  → force transaction_type='HTY'
      * Case 2 — permission for 'VFY' only  → force transaction_type='VFY'
      * Case 3 — permissions for both       → no forcing (dual-scope user)
      * Case 4 — no Transaction Type permission → no forcing (default)

    Bypasses (full access) regardless of User Permission:
      * Administrator
      * Any user with 'System Manager'

    Mutates `filters` in place AND returns it so call sites can chain.
    """
    filters = filters or {}
    user = frappe.session.user if hasattr(frappe, "session") else None
    if user == "Administrator":
        return filters
    roles = set(frappe.get_roles(user)) if user else set()
    if "System Manager" in roles:
        return filters
    if not user:
        return filters

    # Read User Permission rows for Transaction Type. Frappe stores these
    # in `tabUser Permission` keyed on (user, allow, for_value).
    allowed = frappe.db.sql_list(
        """SELECT for_value
           FROM `tabUser Permission`
           WHERE user = %s
             AND allow = 'Transaction Type'
             AND (apply_to_all_doctypes = 1 OR apply_to_all_doctypes IS NULL)""",
        (user,),
    ) or []
    allowed_set = {v for v in allowed if v}

    # Case 4: no Transaction Type permission → no restriction.
    # Case 3: user has permission for BOTH HTY and VFY → no restriction.
    if not allowed_set or {"HTY", "VFY"}.issubset(allowed_set):
        return filters

    # Case 1 / 2: user has exactly one Transaction Type permitted → force it.
    if "HTY" in allowed_set:
        filters["transaction_type"] = "HTY"
    elif "VFY" in allowed_set:
        filters["transaction_type"] = "VFY"
    return filters


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
