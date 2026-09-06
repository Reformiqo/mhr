# Copyright (c) 2026, reformiqo and contributors
# For license information, please see license.txt
#
# MI1-I123 — Subcontracting Stock Tracking
#
# One report for the whole job-work chain:
#
#   Send to Subcontractor  ->  Job Work Received  ->  new lot in the target
#   warehouse  ->  Delivery Note(s)  ->  balance left
#
# built on what MHR actually records (MI1-I50), not on ERPNext's
# `outgoing_stock_entry` / `ste_detail` links, which this site never fills:
#
#   * A Send is a submitted Stock Entry with purpose 'Send to Subcontractor'.
#   * A Receipt is any submitted Stock Entry whose `custom_original_send_entry`
#     names a Send (header link; ERPNext's `outgoing_stock_entry` is honoured
#     too when a site does fill it). Its rows match the Send's rows on
#     `mhr.utilis._subcontract_match_key` (item + supplier batch), exactly as
#     `apply_subcontract_receipt` allocates received qty FIFO onto the Send —
#     this report replays that allocation from the submitted receipts, so
#     cancelled receipts drop out by themselves.
#   * A row of a Receipt that lands stock (target warehouse set) is a LOT:
#     either the sent batch coming back, or a NEW batch named
#     `received_container-received_lot-supplier_batch_no` by
#     `create_receive_batches`. Finished rows that consume nothing are attached
#     to the Send row they belong to by supplier batch, else to the single row
#     the receipt consumed, else to its first consumed row (flagged).
#   * Delivered = submitted Delivery Note rows for that item + batch shipped
#     from the target warehouse; returns carry a negative qty and add back.
#   * Stock in Hand and the subcontractor balance are Serial and Batch Bundle
#     balances (live stock), never `Batch.batch_qty`.
#
# Row grain: Send row x Receipt x lot (batch). Sent Qty and Pending Qty are
# Send-row values repeated on every lot row and totalled once. The total row
# is built here (add_total_row would double-count them).
#
# Everything is set-based: one query per stage, IN-lists chunked at 2000.

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate

from mhr.utilis import _subcontract_match_key

PRECISION = 3
EPS = 0.0005
CHUNK = 2000

STATUS_FULLY_DELIVERED = "Fully Delivered"
STATUS_PARTIALLY_DELIVERED = "Partially Delivered"
STATUS_PENDING = "Pending"
STATUS_PARTIALLY_RECEIVED = "Partially Received"
STATUS_STOCK_AVAILABLE = "Stock Available"
STATUS_FULLY_RECEIVED = "Fully Received"

STATUSES = [
    STATUS_FULLY_DELIVERED,
    STATUS_PARTIALLY_DELIVERED,
    STATUS_PENDING,
    STATUS_PARTIALLY_RECEIVED,
    STATUS_STOCK_AVAILABLE,
    STATUS_FULLY_RECEIVED,
]

SEND_PURPOSE = "Send to Subcontractor"
# Stock Entry Type of a job-work receipt (seeded by
# mhr.patches.v1_0.seed_job_work_received_stock_entry_type). The LIKE also
# catches the older misspelt type that exists on some sites.
RECEIPT_TYPE_LIKE = "Job Work%"

FLAG_NOT_LINKED = "Not linked to a Send to Subcontractor entry"
FLAG_OVER_RECEIVED = "Over Received"
FLAG_FIRST_ROW = "Lot attributed to the first Send row the receipt consumed"

QTY_FIELDS = ("sent_qty", "received_qty", "pending_qty", "delivered_qty", "balance_qty",
              "stock_in_hand", "subcontractor_balance")
# Send-row-level values: totalled on distinct Send rows only (FR-15).
SEND_LEVEL_QTY_FIELDS = ("sent_qty", "pending_qty", "subcontractor_balance")


def execute(filters=None):
    filters = frappe._dict(filters or {})
    validate_filters(filters)
    return get_columns(filters), get_data(filters)


def validate_filters(filters):
    for fieldname, label in (("company", _("Company")), ("from_date", _("From Date")), ("to_date", _("To Date"))):
        if not filters.get(fieldname):
            frappe.throw(_("{0} is mandatory").format(label))
    if getdate(filters.from_date) > getdate(filters.to_date):
        frappe.throw(_("From Date cannot be after To Date"))


# ---------------------------------------------------------------------------
# Columns
# ---------------------------------------------------------------------------

def get_columns(filters=None):
    filters = filters or {}
    columns = [
        {"label": _("Send Entry No."), "fieldname": "send_entry", "fieldtype": "Link", "options": "Stock Entry", "width": 150},
        {"label": _("Send Date"), "fieldname": "send_date", "fieldtype": "Date", "width": 95},
        {"label": _("Source Warehouse"), "fieldname": "source_warehouse", "fieldtype": "Link", "options": "Warehouse", "width": 150},
        {"label": _("Subcontractor Warehouse"), "fieldname": "subcontractor_warehouse", "fieldtype": "Link", "options": "Warehouse", "width": 170},
        {"label": _("Sent Item"), "fieldname": "sent_item", "fieldtype": "Link", "options": "Item", "width": 170},
        {"label": _("Sent Qty"), "fieldname": "sent_qty", "fieldtype": "Float", "width": 95, "precision": PRECISION},
        {"label": _("Job Work Received No."), "fieldname": "receipt_entry", "fieldtype": "Link", "options": "Stock Entry", "width": 160},
        {"label": _("Received Qty"), "fieldname": "received_qty", "fieldtype": "Float", "width": 105, "precision": PRECISION},
        {"label": _("Pending Qty"), "fieldname": "pending_qty", "fieldtype": "Float", "width": 95, "precision": PRECISION},
        {"label": _("Received / New Item"), "fieldname": "received_item", "fieldtype": "Link", "options": "Item", "width": 170},
        {"label": _("Container No."), "fieldname": "container_no", "fieldtype": "Data", "width": 120},
        {"label": _("Lot / Batch No."), "fieldname": "batch_no", "fieldtype": "Link", "options": "Batch", "width": 140},
        {"label": _("Target Warehouse"), "fieldname": "target_warehouse", "fieldtype": "Link", "options": "Warehouse", "width": 150},
        {"label": _("Delivery Note No."), "fieldname": "delivery_note", "fieldtype": "Data", "width": 170},
        {"label": _("Delivered Qty"), "fieldname": "delivered_qty", "fieldtype": "Float", "width": 105, "precision": PRECISION},
        {"label": _("Balance Qty"), "fieldname": "balance_qty", "fieldtype": "Float", "width": 95, "precision": PRECISION},
        {"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 140},
    ]
    if cint(filters.get("show_stock_columns")):
        columns += [
            {"label": _("Stock in Hand (Target WH)"), "fieldname": "stock_in_hand", "fieldtype": "Float", "width": 120, "precision": PRECISION},
            {"label": _("Subcontractor Stock Balance"), "fieldname": "subcontractor_balance", "fieldtype": "Float", "width": 130, "precision": PRECISION},
            {"label": _("Supplier / Job Worker"), "fieldname": "supplier", "fieldtype": "Link", "options": "Supplier", "width": 150},
            {"label": _("Sent Container / Lot"), "fieldname": "sent_container_lot", "fieldtype": "Data", "width": 130},
            {"label": _("Received Lot No."), "fieldname": "lot_no", "fieldtype": "Data", "width": 110},
            {"label": _("Business Line"), "fieldname": "business_line", "fieldtype": "Data", "width": 95},
            {"label": _("UOM"), "fieldname": "uom", "fieldtype": "Link", "options": "UOM", "width": 80},
            {"label": _("Flags"), "fieldname": "flags", "fieldtype": "Data", "width": 260},
        ]
    return columns


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

def get_data(filters):
    sends = get_send_entries(filters)
    send_names = [s.name for s in sends]
    send_rows_by_parent = get_stock_entry_rows(send_names, send_row_conditions(filters))

    receipts = get_receipts(send_names, filters)
    receipt_rows_by_parent = get_stock_entry_rows([r.name for r in receipts])
    receipts_by_send = {}
    for r in receipts:
        receipts_by_send.setdefault(r.send_entry, []).append(r)

    unlinked = get_unlinked_receipts(filters)
    unlinked_rows_by_parent = get_stock_entry_rows([r.name for r in unlinked])

    # Every row's batches — Stock Entry Detail.batch_no, else the bundle.
    all_rows = [row for rows in send_rows_by_parent.values() for row in rows]
    all_rows += [row for rows in receipt_rows_by_parent.values() for row in rows]
    all_rows += [row for rows in unlinked_rows_by_parent.values() for row in rows]
    row_batches = get_row_batches(all_rows)

    batch_names = {b for batches in row_batches.values() for b, _q in batches}
    batch_info = get_batch_info(batch_names)

    rows = []
    for send in sends:
        rows += build_send_rows(
            send, send_rows_by_parent.get(send.name, []),
            receipts_by_send.get(send.name, []), receipt_rows_by_parent,
            row_batches, batch_info,
        )
    for receipt in unlinked:
        rows += build_unlinked_rows(receipt, unlinked_rows_by_parent.get(receipt.name, []), row_batches, batch_info)

    if not rows:
        return []

    # Live stock: lot batch in its target warehouse, sent batch at the subcontractor.
    balances = get_batch_balances({r["batch_no"] for r in rows if r.get("batch_no")} | {b for r in rows for b in r["_sent_batches"]})
    deliveries = get_deliveries({r["batch_no"] for r in rows if r.get("batch_no")}, filters)

    annotate_rows(rows, balances, deliveries)

    rows = apply_row_filters(rows, filters)
    if not rows:
        return []

    if cint(filters.get("expand_delivery_notes")):
        rows = expand_delivery_notes(rows)

    rows.append(build_total_row(rows))
    for r in rows:
        for key in [k for k in r if k.startswith("_")]:
            del r[key]
    return rows


# --- Stage 1: Send entries -------------------------------------------------

def get_send_entries(filters):
    """Submitted Send-to-Subcontractor entries in the date range. Goes through
    frappe.get_list so the user's Company / Warehouse user permissions apply
    (FR-17)."""
    conditions = [
        ["Stock Entry", "docstatus", "=", 1],
        ["Stock Entry", "purpose", "=", SEND_PURPOSE],
        ["Stock Entry", "company", "=", filters.company],
        ["Stock Entry", "posting_date", ">=", filters.from_date],
        ["Stock Entry", "posting_date", "<=", filters.to_date],
    ]
    if filters.get("supplier"):
        conditions.append(["Stock Entry", "supplier", "=", filters.supplier])
    if filters.get("send_entry"):
        conditions.append(["Stock Entry", "name", "=", filters.send_entry])
    or_filters = business_line_or_filters(filters)
    return frappe.get_list(
        "Stock Entry",
        filters=conditions,
        or_filters=or_filters,
        fields=[
            "name", "posting_date", "posting_time", "supplier", "company", "transaction_type",
            "from_warehouse", "to_warehouse", "custom_container_number", "custom_lot_no",
            "custom_subcontract_status",
        ],
        order_by="posting_date desc, posting_time desc, name desc",
        limit_page_length=0,
    )


def business_line_or_filters(filters):
    """HTY = transaction_type 'HTY'; VFY = 'VFY' or blank — the same
    IFNULL -> VFY rule the Delivery Challan / Delivery Trip reports use for
    legacy documents. 'All' / blank filters nothing."""
    line = (filters.get("business_line") or "").strip()
    if line == "HTY":
        return [["Stock Entry", "transaction_type", "=", "HTY"]]
    if line == "VFY":
        return [
            ["Stock Entry", "transaction_type", "=", "VFY"],
            ["Stock Entry", "transaction_type", "is", "not set"],
        ]
    return None


def send_row_conditions(filters):
    conditions, params = [], {}
    if filters.get("source_warehouse"):
        conditions.append("sed.s_warehouse = %(source_warehouse)s")
        params["source_warehouse"] = filters.source_warehouse
    if filters.get("subcontractor_warehouse"):
        conditions.append("sed.t_warehouse = %(subcontractor_warehouse)s")
        params["subcontractor_warehouse"] = filters.subcontractor_warehouse
    if filters.get("sent_item"):
        conditions.append("sed.item_code = %(sent_item)s")
        params["sent_item"] = filters.sent_item
    return conditions, params


def get_stock_entry_rows(parents, extra=None):
    """Rows of the given Stock Entries, keyed by parent, in idx order."""
    out = {}
    if not parents:
        return out
    conditions, params = extra or ([], {})
    where = (" AND " + " AND ".join(conditions)) if conditions else ""
    for chunk in _chunks(list(parents)):
        rows = frappe.db.sql(
            f"""
            SELECT sed.name, sed.parent, sed.idx, sed.item_code, sed.item_name,
                   sed.qty, sed.transfer_qty, sed.conversion_factor, sed.stock_uom, sed.uom,
                   sed.s_warehouse, sed.t_warehouse, sed.batch_no, sed.serial_and_batch_bundle,
                   sed.custom_supplier_batch_no, sed.custom_received_qty, sed.custom_pending_qty,
                   sed.ste_detail, sed.against_stock_entry
            FROM `tabStock Entry Detail` sed
            WHERE sed.parent IN %(parents)s AND sed.parenttype = 'Stock Entry'{where}
            ORDER BY sed.parent, sed.idx
            """,
            {"parents": tuple(chunk), **params},
            as_dict=True,
        )
        for r in rows:
            r.stock_qty = _stock_qty(r)
            out.setdefault(r.parent, []).append(r)
    return out


def _stock_qty(row):
    """transfer_qty is the stock-UOM quantity (BR-05); legacy rows may hold 0."""
    if flt(row.transfer_qty):
        return flt(row.transfer_qty)
    return flt(row.qty) * (flt(row.conversion_factor) or 1.0)


# --- Stage 2: Receipts -----------------------------------------------------

def get_receipts(send_names, filters):
    """Submitted receipts against the Sends, in posting order — the order
    apply_subcontract_receipt saw them, so the FIFO replay matches."""
    if not send_names:
        return []
    out = []
    date_sql, params = receipt_date_clause(filters)
    for chunk in _chunks(send_names):
        out += frappe.db.sql(
            f"""
            SELECT se.name, se.posting_date, se.posting_time, se.creation, se.purpose,
                   se.stock_entry_type, se.custom_received_container_no, se.custom_received_lot_no,
                   se.transaction_type, se.company,
                   COALESCE(NULLIF(se.custom_original_send_entry, ''), se.outgoing_stock_entry) AS send_entry
            FROM `tabStock Entry` se
            WHERE se.docstatus = 1
              AND (se.custom_original_send_entry IN %(sends)s OR se.outgoing_stock_entry IN %(sends)s)
              {date_sql}
            ORDER BY se.posting_date, se.posting_time, se.creation
            """,
            {"sends": tuple(chunk), **params},
            as_dict=True,
        )
    out.sort(key=lambda r: (str(r.posting_date), str(r.posting_time), str(r.creation)))
    return out


def receipt_date_clause(filters):
    """BR-13: receipts and deliveries after To Date still count so balances
    are true as on today — unless 'Restrict receipts / deliveries to date
    range' is ticked."""
    if cint(filters.get("restrict_to_date_range")):
        return ("AND se.posting_date BETWEEN %(from_date)s AND %(to_date)s",
                {"from_date": filters.from_date, "to_date": filters.to_date})
    return "", {}


def get_unlinked_receipts(filters):
    """Job-work-typed receipts with no Send behind them (UAT-18). Nothing
    links them to a Send date, so the report's own date range applies to
    their posting date."""
    or_filters = business_line_or_filters(filters)
    conditions = [
        ["Stock Entry", "docstatus", "=", 1],
        ["Stock Entry", "company", "=", filters.company],
        ["Stock Entry", "stock_entry_type", "like", RECEIPT_TYPE_LIKE],
        ["Stock Entry", "custom_original_send_entry", "is", "not set"],
        ["Stock Entry", "outgoing_stock_entry", "is", "not set"],
        ["Stock Entry", "posting_date", ">=", filters.from_date],
        ["Stock Entry", "posting_date", "<=", filters.to_date],
    ]
    if filters.get("send_entry") or filters.get("supplier") or filters.get("sent_item") \
            or filters.get("source_warehouse") or filters.get("subcontractor_warehouse"):
        # Those filters describe the Send side, which an unlinked receipt has none of.
        return []
    return frappe.get_list(
        "Stock Entry",
        filters=conditions,
        or_filters=or_filters,
        fields=[
            "name", "posting_date", "posting_time", "creation", "purpose", "stock_entry_type",
            "custom_received_container_no", "custom_received_lot_no", "transaction_type", "company",
        ],
        order_by="posting_date desc, posting_time desc, name desc",
        limit_page_length=0,
    )


# --- Batches ---------------------------------------------------------------

def get_row_batches(rows):
    """{row name: [(batch_no, stock qty), ...]} — the row's batch_no when it
    carries one (MHR always does), else the batches inside its Serial and
    Batch Bundle (sheet 6 item 10)."""
    out = {}
    bundle_rows = []
    for r in rows:
        if r.batch_no:
            out[r.name] = [(r.batch_no, r.stock_qty)]
        elif r.serial_and_batch_bundle:
            bundle_rows.append(r)
        else:
            out[r.name] = []
    by_bundle = {r.serial_and_batch_bundle: r for r in bundle_rows}
    for chunk in _chunks(list(by_bundle)):
        entries = frappe.db.sql(
            """
            SELECT parent, batch_no, SUM(ABS(qty)) AS qty
            FROM `tabSerial and Batch Entry`
            WHERE parent IN %(bundles)s AND IFNULL(batch_no, '') != ''
            GROUP BY parent, batch_no
            """,
            {"bundles": tuple(chunk)},
            as_dict=True,
        )
        for e in entries:
            row = by_bundle[e.parent]
            out.setdefault(row.name, []).append((e.batch_no, flt(e.qty)))
    for r in bundle_rows:
        out.setdefault(r.name, [])
    return out


def get_batch_info(batch_names):
    out = {}
    for chunk in _chunks(list(batch_names)):
        for b in frappe.db.sql(
            """
            SELECT name, item, custom_container_no, custom_lot_no, custom_supplier_batch_no
            FROM `tabBatch` WHERE name IN %(names)s
            """,
            {"names": tuple(chunk)},
            as_dict=True,
        ):
            out[b.name] = b
    return out


def get_batch_balances(batch_names):
    """{(batch, warehouse): qty} from the Serial and Batch Bundle — live
    stock, the same source as mhr.utilis._batch_balance_in_warehouse — plus
    any pre-bundle Stock Ledger rows that name the batch directly."""
    out = {}
    names = [b for b in batch_names if b]
    for chunk in _chunks(names):
        for r in frappe.db.sql(
            """
            SELECT sbe.batch_no, sbb.warehouse, SUM(sbe.qty) AS qty
            FROM `tabSerial and Batch Entry` sbe
            INNER JOIN `tabSerial and Batch Bundle` sbb ON sbb.name = sbe.parent
            WHERE sbe.batch_no IN %(names)s
              AND sbb.docstatus = 1 AND sbb.is_cancelled = 0
              AND sbb.type_of_transaction IN ('Inward', 'Outward')
            GROUP BY sbe.batch_no, sbb.warehouse
            """,
            {"names": tuple(chunk)},
            as_dict=True,
        ):
            out[(r.batch_no, r.warehouse)] = out.get((r.batch_no, r.warehouse), 0.0) + flt(r.qty)
        for r in frappe.db.sql(
            """
            SELECT batch_no, warehouse, SUM(actual_qty) AS qty
            FROM `tabStock Ledger Entry`
            WHERE batch_no IN %(names)s AND is_cancelled = 0
              AND IFNULL(serial_and_batch_bundle, '') = ''
            GROUP BY batch_no, warehouse
            """,
            {"names": tuple(chunk)},
            as_dict=True,
        ):
            out[(r.batch_no, r.warehouse)] = out.get((r.batch_no, r.warehouse), 0.0) + flt(r.qty)
    return out


def get_deliveries(batch_names, filters):
    """{batch: [ {delivery_note, item_code, warehouse, qty, posting_date}, ...]}
    from submitted Delivery Notes. Returns (is_return) carry negative qty and
    add back (BR-08). Rows whose batch lives only in the bundle are read
    from Serial and Batch Entry (outward qty is negative there)."""
    out = {}
    names = [b for b in batch_names if b]
    date_sql, params = "", {}
    if cint(filters.get("restrict_to_date_range")):
        date_sql = "AND dn.posting_date BETWEEN %(from_date)s AND %(to_date)s"
        params = {"from_date": filters.from_date, "to_date": filters.to_date}
    for chunk in _chunks(names):
        rows = frappe.db.sql(
            f"""
            SELECT dni.parent AS delivery_note, dni.item_code, dni.warehouse, dni.batch_no,
                   dni.stock_qty AS qty, dn.posting_date, dn.is_return
            FROM `tabDelivery Note Item` dni
            INNER JOIN `tabDelivery Note` dn ON dn.name = dni.parent
            WHERE dn.docstatus = 1 AND dni.batch_no IN %(names)s {date_sql}
            UNION ALL
            SELECT dni.parent, dni.item_code, dni.warehouse, sbe.batch_no,
                   -sbe.qty, dn.posting_date, dn.is_return
            FROM `tabSerial and Batch Entry` sbe
            INNER JOIN `tabDelivery Note Item` dni ON dni.serial_and_batch_bundle = sbe.parent
            INNER JOIN `tabDelivery Note` dn ON dn.name = dni.parent
            WHERE dn.docstatus = 1 AND sbe.batch_no IN %(names)s
              AND IFNULL(dni.batch_no, '') = '' {date_sql}
            ORDER BY posting_date, delivery_note
            """,
            {"names": tuple(chunk), **params},
            as_dict=True,
        )
        for r in rows:
            r.qty = flt(r.qty)
            out.setdefault(r.batch_no, []).append(r)
    return out


# --- Row building ----------------------------------------------------------

def build_send_rows(send, send_rows, receipts, receipt_rows_by_parent, row_batches, batch_info):
    """All report rows of one Send entry."""
    if not send_rows:
        return []

    # FIFO replay of apply_subcontract_receipt over the submitted receipts.
    by_key = {}
    for s in send_rows:
        by_key.setdefault(_subcontract_match_key(s), []).append(s)
        s._received = 0.0
        s._alloc = {}          # receipt row name -> qty allocated to this send row
    by_name = {s.name: s for s in send_rows}
    receipt_rows = {r.name: receipt_rows_by_parent.get(r.name, []) for r in receipts}

    for receipt in receipts:
        for rr in receipt_rows[receipt.name]:
            remaining = rr.stock_qty
            if remaining <= 0:
                continue
            if rr.ste_detail and rr.ste_detail in by_name:
                targets = [by_name[rr.ste_detail]]          # ERPNext row-level link
            else:
                targets = by_key.get(_subcontract_match_key(rr), [])
            if not targets:
                continue                                    # finished / new material
            for s in targets:
                if remaining <= EPS:
                    break
                room = s.stock_qty - s._received
                if room <= 0:
                    continue
                take = min(remaining, room)
                s._alloc[rr.name] = s._alloc.get(rr.name, 0.0) + take
                s._received += take
                remaining -= take
            if remaining > EPS:                              # tolerance overflow -> first row
                s = targets[0]
                s._alloc[rr.name] = s._alloc.get(rr.name, 0.0) + remaining
                s._received += remaining

    # Which receipt rows land stock without consuming a Send row (finished goods).
    finished_by_receipt = {}
    consumed_rows = {rr_name for s in send_rows for rr_name in s._alloc}
    for receipt in receipts:
        finished_by_receipt[receipt.name] = [
            rr for rr in receipt_rows[receipt.name]
            if rr.t_warehouse and rr.name not in consumed_rows and rr.stock_qty > 0
        ]

    rows = []
    for s in send_rows:
        sent_batches = [b for b, _q in row_batches.get(s.name, [])]
        received_total = s._received
        pending = max(0.0, s.stock_qty - received_total)
        base_flags = []
        if received_total > s.stock_qty + EPS:
            base_flags.append(FLAG_OVER_RECEIVED)
        recorded = flt(s.custom_received_qty) * (flt(s.conversion_factor) or 1.0)
        if abs(recorded - received_total) > EPS:
            base_flags.append(_("Send row records received {0}").format(flt(recorded, PRECISION)))
        first_batch = batch_info.get(sent_batches[0]) if sent_batches else None
        sent_container = (first_batch and first_batch.custom_container_no) or send.custom_container_number or ""
        sent_lot = (first_batch and first_batch.custom_lot_no) or send.custom_lot_no or ""
        base = {
            "send_entry": send.name,
            "send_date": send.posting_date,
            "source_warehouse": s.s_warehouse,
            "subcontractor_warehouse": s.t_warehouse,
            "sent_item": s.item_code,
            "sent_qty": flt(s.stock_qty, PRECISION),
            "pending_qty": flt(pending, PRECISION),
            "supplier": send.supplier,
            "sent_container_lot": " / ".join(x for x in (sent_container, sent_lot) if x),
            "business_line": send.transaction_type or "VFY",
            "uom": s.stock_uom,
            "_send_row": s.name,
            "_sent_batches": sent_batches,
            "_flags": list(base_flags),
        }

        emitted = 0
        for receipt in receipts:
            consumed_here = [(rr, s._alloc[rr.name]) for rr in receipt_rows[receipt.name] if rr.name in s._alloc]
            finished_here = [rr for rr in finished_by_receipt[receipt.name] if _attribute_finished(rr, s, send_rows, receipt_rows[receipt.name])]
            if not consumed_here and not finished_here:
                continue
            for rr, qty in consumed_here:
                flags = []
                if rr.s_warehouse and s.t_warehouse and rr.s_warehouse != s.t_warehouse:
                    flags.append(_("Receipt source warehouse {0} differs from the subcontractor warehouse").format(rr.s_warehouse))
                if rr.t_warehouse:
                    for batch_no, portion in _split_by_batch(rr, qty, row_batches):
                        rows.append(_lot_row(base, receipt, rr, batch_no, portion, batch_info, flags))
                        emitted += 1
                elif not finished_here:
                    # Consumed at the subcontractor, nothing landed (loss / write-off).
                    rows.append(_receipt_only_row(base, receipt, rr, qty, flags))
                    emitted += 1
            for rr in finished_here:
                flags = [] if len(_consumed_send_rows(receipt_rows[receipt.name], send_rows)) <= 1 or _sbn_match(rr, s) else [FLAG_FIRST_ROW]
                for batch_no, portion in _split_by_batch(rr, rr.stock_qty, row_batches):
                    rows.append(_lot_row(base, receipt, rr, batch_no, portion, batch_info, flags))
                    emitted += 1
        if not emitted:
            rows.append(_send_only_row(base))
    return rows


def _consumed_send_rows(receipt_rows, send_rows):
    names = {rr.name for rr in receipt_rows}
    return [s for s in send_rows if any(n in names for n in s._alloc)]


def _sbn_match(rr, s):
    return bool(rr.custom_supplier_batch_no) and (rr.custom_supplier_batch_no or "") == (s.custom_supplier_batch_no or "")


def _attribute_finished(rr, s, send_rows, receipt_rows):
    """Does finished row `rr` belong under Send row `s`? By supplier batch
    first; else the single Send row this receipt consumed (or the single row
    of the Send); else the first consumed row."""
    candidates = _consumed_send_rows(receipt_rows, send_rows) or list(send_rows)
    by_sbn = [c for c in candidates if _sbn_match(rr, c)]
    if by_sbn:
        return by_sbn[0].name == s.name
    return candidates[0].name == s.name


def _split_by_batch(rr, qty, row_batches):
    """Spread `qty` of receipt row `rr` over its batches in proportion (a
    row normally carries exactly one batch)."""
    batches = row_batches.get(rr.name) or [(None, rr.stock_qty)]
    total = sum(q for _b, q in batches) or rr.stock_qty or 1.0
    return [(b, qty * (q / total)) for b, q in batches]


def _lot_row(base, receipt, rr, batch_no, qty, batch_info, flags):
    info = batch_info.get(batch_no) or frappe._dict()
    row = dict(base)
    row.update({
        "receipt_entry": receipt.name,
        "received_qty": flt(qty, PRECISION),
        "received_item": rr.item_code,
        "container_no": info.get("custom_container_no") or receipt.custom_received_container_no or "",
        "lot_no": info.get("custom_lot_no") or receipt.custom_received_lot_no or "",
        "batch_no": batch_no,
        "target_warehouse": rr.t_warehouse,
        "_flags": base["_flags"] + flags,
    })
    return row


def _receipt_only_row(base, receipt, rr, qty, flags):
    row = dict(base)
    row.update({
        "receipt_entry": receipt.name,
        "received_qty": flt(qty, PRECISION),
        "received_item": None, "container_no": "", "lot_no": "", "batch_no": None, "target_warehouse": None,
        "_flags": base["_flags"] + flags + [_("Consumed at the subcontractor, no stock landed")],
    })
    return row


def _send_only_row(base):
    row = dict(base)
    row.update({
        "receipt_entry": None, "received_qty": 0.0, "received_item": None, "container_no": "",
        "lot_no": "", "batch_no": None, "target_warehouse": None,
    })
    return row


def build_unlinked_rows(receipt, receipt_rows, row_batches, batch_info):
    rows = []
    for rr in receipt_rows:
        if not rr.t_warehouse or rr.stock_qty <= 0:
            continue
        for batch_no, portion in _split_by_batch(rr, rr.stock_qty, row_batches):
            info = batch_info.get(batch_no) or frappe._dict()
            rows.append({
                "send_entry": None, "send_date": None, "source_warehouse": rr.s_warehouse,
                "subcontractor_warehouse": None, "sent_item": None, "sent_qty": 0.0,
                "receipt_entry": receipt.name, "received_qty": flt(portion, PRECISION), "pending_qty": 0.0,
                "received_item": rr.item_code,
                "container_no": info.get("custom_container_no") or receipt.custom_received_container_no or "",
                "lot_no": info.get("custom_lot_no") or receipt.custom_received_lot_no or "",
                "batch_no": batch_no, "target_warehouse": rr.t_warehouse,
                "supplier": None, "sent_container_lot": "", "business_line": receipt.transaction_type or "VFY",
                "uom": rr.stock_uom,
                "_send_row": None, "_sent_batches": [], "_flags": [FLAG_NOT_LINKED],
            })
    return rows


# --- Annotation: deliveries, stock, status ---------------------------------

def annotate_rows(rows, balances, deliveries):
    """Deliveries, live stock and status for every row.

    Rows that share a lot — the same batch landing in the same target
    warehouse, e.g. a sent batch returned in two instalments — form a group:
    the lot's Delivery Note lines are allocated across the group's rows in
    receipt order (a return drains the last row first), Stock in Hand is the
    lot's live balance and is shown on the group's first row only, and the
    reconciliation flags compare the lot as a whole. Nothing is counted twice.
    """
    groups = {}
    for r in rows:
        key = (r.get("batch_no"), r.get("target_warehouse"), r.get("received_item"))
        if key[0] and key[1]:
            groups.setdefault(key, []).append(r)
        else:
            r["_dn_rows"] = []

    for (batch_no, target, item), members in groups.items():
        lines = deliveries.get(batch_no, [])
        own = [d for d in lines if d.item_code == item and d.warehouse == target]
        elsewhere = [d for d in lines if not (d.item_code == item and d.warehouse == target)]
        _allocate_deliveries(members, own)

        stock_in_hand = balances.get((batch_no, target), 0.0)
        lot_received = sum(flt(m.get("received_qty")) for m in members)
        lot_delivered = sum(d.qty for d in own)
        lot_flags = []
        if abs(stock_in_hand - (lot_received - lot_delivered)) > EPS:
            lot_flags.append(_("Stock in hand {0} differs from balance (moved by another document)").format(
                flt(stock_in_hand, PRECISION)))
        if lot_received - lot_delivered < -EPS:
            lot_flags.append(_("Delivered more than received"))
        if elsewhere:
            lot_flags.append(_("Also delivered {0} outside the target warehouse ({1})").format(
                flt(sum(d.qty for d in elsewhere), PRECISION),
                ", ".join(_unique([d.warehouse or "" for d in elsewhere]))))
        for i, m in enumerate(members):
            m["_stock_in_hand"] = stock_in_hand
            m["stock_in_hand"] = flt(stock_in_hand, PRECISION) if i == 0 else None
            if i == 0:
                m["_flags"] = m["_flags"] + lot_flags

    for r in rows:
        dn_rows = r.get("_dn_rows") or []
        delivered = sum(q for _d, q in dn_rows)
        received = flt(r.get("received_qty"))
        has_lot = bool(r.get("batch_no") and r.get("target_warehouse"))
        balance = received - delivered if has_lot else 0.0
        sub_balance = sum(balances.get((b, r["subcontractor_warehouse"]), 0.0) for b in r["_sent_batches"]) \
            if r.get("subcontractor_warehouse") else 0.0
        r["delivery_note"] = ", ".join(_unique([d.delivery_note for d, _q in dn_rows]))
        r["delivered_qty"] = flt(delivered, PRECISION)
        r["balance_qty"] = flt(balance, PRECISION)
        r.setdefault("stock_in_hand", 0.0)
        r.setdefault("_stock_in_hand", 0.0)
        r["subcontractor_balance"] = flt(sub_balance, PRECISION)
        r["flags"] = "; ".join(r["_flags"])
        r["status"] = derive_status(received, delivered, balance, flt(r.get("pending_qty")), r["_stock_in_hand"])


def _allocate_deliveries(members, lines):
    """Spread a lot's Delivery Note lines (posting order) over the rows that
    received it: a shipment fills rows in order up to what each received, the
    last row taking any excess; a return (negative line) drains from the last
    row backwards. Each row ends with its own [(line, qty)] list."""
    for m in members:
        m["_dn_rows"] = []
        m["_delivered"] = 0.0
    for d in lines:
        qty = d.qty
        if qty >= 0:
            for i, m in enumerate(members):
                if qty <= EPS:
                    break
                room = flt(m.get("received_qty")) - m["_delivered"]
                take = qty if i == len(members) - 1 else max(0.0, min(qty, room))
                if take <= 0:
                    continue
                m["_dn_rows"].append((d, take))
                m["_delivered"] += take
                qty -= take
        else:
            back = -qty
            for i, m in enumerate(reversed(members)):
                if back <= EPS:
                    break
                give = back if i == len(members) - 1 else min(back, max(0.0, m["_delivered"]))
                if give <= 0:
                    continue
                m["_dn_rows"].append((d, -give))
                m["_delivered"] -= give
                back -= give


def derive_status(received, delivered, balance, pending, stock_in_hand):
    """Sheet 7, first match wins. Delivery is judged before receipt because a
    shipped lot is by definition received; 'Pending' and 'Partially Received'
    describe the Send row, the other four the received lot."""
    if received > EPS and delivered > EPS and balance <= EPS:
        return STATUS_FULLY_DELIVERED
    if delivered > EPS and balance > EPS:
        return STATUS_PARTIALLY_DELIVERED
    if received <= EPS:
        return STATUS_PENDING
    if pending > EPS and delivered <= EPS:
        return STATUS_PARTIALLY_RECEIVED
    if pending <= EPS and delivered <= EPS and stock_in_hand > EPS:
        return STATUS_STOCK_AVAILABLE
    return STATUS_FULLY_RECEIVED


# --- Row-level filters, expansion, totals ----------------------------------

def apply_row_filters(rows, filters):
    statuses = filters.get("status") or []
    if isinstance(statuses, str):
        statuses = [s for s in (frappe.parse_json(statuses) if statuses.startswith("[") else statuses.split(",")) if s]
    statuses = {s.strip() for s in statuses if s and s.strip()}
    container = (filters.get("container_no") or "").strip().lower()
    batch = filters.get("batch_no")
    received_item = filters.get("received_item")
    target = filters.get("target_warehouse")
    only_pending = cint(filters.get("only_pending"))
    only_undelivered = cint(filters.get("only_undelivered"))

    def keep(r):
        if statuses and r["status"] not in statuses:
            return False
        if only_pending and flt(r["pending_qty"]) <= EPS:
            return False
        if only_undelivered and flt(r["balance_qty"]) <= EPS:
            return False
        if received_item and r.get("received_item") != received_item:
            return False
        if target and r.get("target_warehouse") != target:
            return False
        if batch and batch != r.get("batch_no") and batch not in r["_sent_batches"]:
            return False
        if container:
            haystack = " ".join([r.get("container_no") or "", r.get("sent_container_lot") or ""]).lower()
            if container not in haystack:
                return False
        return True

    return [r for r in rows if keep(r)]


def expand_delivery_notes(rows):
    """One row per Delivery Note line; Received Qty, Balance Qty and Stock in
    Hand only on the first row of each lot so totals do not change (UAT-08)."""
    out = []
    for r in rows:
        dn_rows = r.get("_dn_rows") or []
        if len(dn_rows) <= 1:
            out.append(r)
            continue
        for i, (d, qty) in enumerate(dn_rows):
            line = dict(r)
            line["delivery_note"] = d.delivery_note
            line["delivered_qty"] = flt(qty, PRECISION)
            if i > 0:
                line["received_qty"] = None
                line["balance_qty"] = None
                line["stock_in_hand"] = None
            out.append(line)
    return out


def build_total_row(rows):
    """FR-15: Sent / Pending (and the subcontractor balance) once per Send
    row; the lot quantities over every displayed row."""
    total = {c: None for c in ("send_entry", "send_date", "source_warehouse", "subcontractor_warehouse", "sent_item",
                               "receipt_entry", "received_item", "container_no", "batch_no", "target_warehouse",
                               "delivery_note", "status", "supplier", "sent_container_lot", "lot_no",
                               "business_line", "uom", "flags")}
    total["send_entry"] = _("Total")
    total["is_total_row"] = 1
    seen_send_rows = set()
    for f in QTY_FIELDS:
        total[f] = 0.0
    for r in rows:
        for f in QTY_FIELDS:
            if f in SEND_LEVEL_QTY_FIELDS:
                continue
            total[f] += flt(r.get(f))
        key = r.get("_send_row")
        if key and key not in seen_send_rows:
            seen_send_rows.add(key)
            for f in SEND_LEVEL_QTY_FIELDS:
                total[f] += flt(r.get(f))
    for f in QTY_FIELDS:
        total[f] = flt(total[f], PRECISION)
    total["_send_row"] = None
    total["_sent_batches"] = []
    total["_flags"] = []
    return total


def _unique(values):
    seen, out = set(), []
    for v in values:
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    return out


def _chunks(values, size=CHUNK):
    values = list(values)
    for i in range(0, len(values), size):
        yield values[i:i + size]
