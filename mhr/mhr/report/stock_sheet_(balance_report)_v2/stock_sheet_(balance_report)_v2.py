# Copyright (c) 2026, reformiqo and contributors
# For license information, please see license.txt
#
# MI1-I135 (Raj 2026-09-10): "an exact replica of Stock Sheet (Balance
# Report) ... as a new report without modifying the existing report", with
# the changes in MHR_Stock_Sheet_Book2_Reviewed_v3.0.xlsx (the "Book2" FRD,
# reviewed and corrected to v3.0 by Reformiqo's own analyst before this was
# built). Four new columns — In Qty, Out Qty, GR Received, Job work Send Qty.
#
# MI1-I136 (Change Request, 2026-09-13, "For implementation"): re-specified
# the exact source/formula for all nine of the report's quantity columns.
# One of its three changes is still in effect: Booked / Delivered / Pending
# Qty show the RAW, un-reduced Sales Order figures instead of the original
# report's "effective/released" booking. Its other two changes — In Qty as
# Container.total_net_weight, and the row grain collapsed to one row per
# (Container, Lot) — are REVERTED by MI1-I143 below.
#
# MI1-I143 (2026-09-15, live review with Raj, two passes):
#
#   Pass 1 — "Item and Cone values are currently getting combined ...
#   reflected separately for each individual stock/batch record, exactly
#   like the original": row grain reverted to one row per (Container, Item,
#   Lot, Cone, Pulp, Lusture, Glue, Grade) — the original report's own
#   grouping key — instead of one collapsed row per (Container, Lot).
#
#   Pass 2 — Raj then walked through the exact source of every column on a
#   live call:
#     - In Qty and Out Qty: computed the SAME way the existing "Container
#       Report" (mhr/mhr/report/container_report) computes them — summed
#       straight from the real Serial and Batch Bundle ledger (Serial and
#       Batch Entry rows, `type_of_transaction` = Inward / Outward), not
#       from Batch Items / Delivery Note Item / Stock Entry Detail, and not
#       Container.total_net_weight.
#     - GR Received: any return against that container (Delivery Note
#       is_return=1) — unchanged from MI1-I135/136.
#     - Job work Send Qty: Stock Entry of type Send to Subcontractor against
#       that container — unchanged from MI1-I135/136.
#     - Balance Qty: In Qty − Out Qty + GR Received − Job work Send Qty —
#       the ORIGINAL MI1-I135/136 formula, confirmed again explicitly (not
#       the original non-v2 report's live-balance approach).
#     - Booked / Available / Delivered / Pending Qty: unchanged (already
#       matched — see sales_order_booking_state / get_data's own detail).
#
#   Double-counting fix (confirmed live in the same session, before
#   implementing): ERPNext tags ANY stock movement back into a warehouse
#   "Inward" purely from the posting Stock Ledger Entry's actual_qty sign
#   (erpnext/stock/serial_batch_bundle.py — no separate "return"
#   classification exists), so a return Delivery Note posts an Inward
#   bundle on the SAME batch it was shipped from (confirmed on real data:
#   MAT-DN-RET-2026-00014's bundle is `type_of_transaction=Inward`, same
#   batch_no as the original shipment). A naive "In Qty = every Inward
#   bundle" would already include every return, and adding GR Received a
#   second time in the Balance formula would double it. Per Raj's explicit
#   choice: In Qty excludes any Inward bundle whose `voucher_type` is
#   'Delivery Note' (the only source of a return-driven Inward bundle in
#   this domain — real Container Inward comes through Purchase Receipt,
#   Job Work Received through Stock Entry) — see get_ledger_in_out().
#
# The generic balance/warehouse primitives (whole-site balance map, Redis
# caching, FORCE INDEX hint, live warehouse resolution) are IMPORTED from
# the original report rather than duplicated — they are unrelated to what
# any of these change requests touch, and any future fix to them (index
# maintenance, a new warehouse rule) should not need to be applied twice.
# Live-SBB balance is still read here for exactly one thing: which of a
# group's batches currently hold stock, to resolve Accepted Warehouse — it
# does not feed Balance Qty (that comes from the ledger formula above).
#
# Known limitation, carried over verbatim from the FRD's own "Issues to
# Settle" sheet (Issue 7): In Qty / Out Qty / GR Received / Job Work Send
# Qty only cover Container Inward, Job Work Received, Delivery, Sales
# Return and Send to Subcontractor. They do not cover Purchase Receipt (for
# any OTHER reason than Container Inward), Material Issue, Stock
# Reconciliation, transfers between the company's own warehouses, or scrap.
# If any of those touch VFY/HTY stock, Balance Qty here can disagree with
# the original Stock Sheet (Balance Report) and with Stock Ledger for the
# same row — that is why the original is left untouched rather than
# modified in place.

import importlib

import frappe
from frappe.utils import add_days, cint, date_diff, flt, getdate, today
from collections import defaultdict

# The original report's folder/module name carries literal parentheses, which
# a plain `from ... import ...` statement cannot parse (SyntaxError) — the
# restriction is on the Python grammar, not on `importlib`, which frappe's
# own dotted-path resolvers (frappe.get_attr / frappe.get_module) already
# rely on for this exact module (see the "hourly" scheduler_events string in
# hooks.py). Importing this way, once, at module load, keeps every call site
# below reading like a normal import.
_original = importlib.import_module(
    "mhr.mhr.report.stock_sheet_(balance_report).stock_sheet_(balance_report)"
)
get_batch_warehouse_balances = _original.get_batch_warehouse_balances
get_batch_balances = _original.get_batch_balances
get_all_warehouse_balances = _original.get_all_warehouse_balances
live_warehouses_by_batch = _original.live_warehouses_by_batch
live_warehouses = _original.live_warehouses
get_batch_rows = _original.get_batch_rows
strip_prefix = _original.strip_prefix
WHOLE_SITE_FROM = _original.WHOLE_SITE_FROM
_original_get_columns = _original.get_columns

REPORT_NAME = "STOCK SHEET (BALANCE REPORT) v2"


def execute(filters=None):
    filters = filters or {}
    from mhr.utilis import enforce_role_scoped_transaction_type
    filters = enforce_role_scoped_transaction_type(filters)
    columns = get_columns(filters)
    data = get_data(filters)
    keep_inline()
    return columns, data


def keep_inline():
    """Unconditionally undo any `prepared_report=1` flip once this function
    has actually produced real data — regardless of what the flag read as
    when this call started.

    MI1-I135 (2026-09-12, "still the report is not fixed", round 2): the
    first version of this safety valve only reset the flag when THIS same
    call had itself observed the flag as 0 at the start (`started_inline`).
    That gate created a closed loop with no way out: frappe's own dispatcher
    (`frappe.desk.query_report.run`) only calls this module's `execute()`
    directly when `Report.prepared_report` is already 0 at the moment a
    request starts — the instant it reads 1, every subsequent open (and even
    the "Rebuild" button, which just enqueues `frappe.core.doctype.
    prepared_report.prepared_report.generate_report` as a background job)
    is routed around `execute()` entirely, or into a worker invocation of
    `execute()` that *also* sees the flag already at 1 and so, under the old
    gate, *also* declined to reset it. A live stress test confirmed the
    trap directly: after one slow run flipped the flag, a follow-up request
    inline in the SAME process (bypassing frappe's own dispatcher, so it did
    reach `execute()`) still saw `prepared_report=1` afterward — proving the
    gate, not just the scheduler being off, was the reason nothing ever
    unstuck it. There is no real "administrator deliberately wants this
    report to stay in background mode" use case for either balance report —
    every ticket on this flag (MI1-I119, MI1-I131, MI1-I135 twice) has been
    a client complaint about it getting stuck, never a request to keep it
    that way — so resetting unconditionally on every successful run, however
    it was reached, is the correct behaviour, not a workaround.

    Still an atomic conditional UPDATE, not a read-then-write: frappe's 15s
    watcher (`report.py :: enable_prepared_report`) runs in a background
    thread on its own connection and commits `prepared_report=1` mid-run: a
    plain SELECT inside this request's own REPEATABLE READ transaction can
    still see the pre-flip 0 even after that commit landed, but a DML
    statement's WHERE clause always evaluates against the latest committed
    row ("current read"), so this single UPDATE reliably finds and undoes
    the flip no matter when this transaction started.
    """
    try:
        frappe.db.sql(
            "UPDATE `tabReport` SET prepared_report=0 WHERE name=%s AND prepared_report=1",
            (REPORT_NAME,),
        )
    except Exception:
        frappe.log_error(title="Stock Sheet (Balance Report) v2: could not keep the report inline")


def get_columns(filters=None):
    """The original's columns, with the FRD's four new columns spliced in
    immediately after Glue and before Balance Qty — everything else in the
    same order, same labels, same HTY/VFY swapping."""
    columns = _original_get_columns(filters)
    glue_idx = next(i for i, c in enumerate(columns) if c["fieldname"] == "Glue")
    new_columns = [
        {"label": "In Qty", "fieldname": "In Qty", "fieldtype": "Data", "width": 100},
        {"label": "Out Qty", "fieldname": "Out Qty", "fieldtype": "Data", "width": 100},
        {"label": "GR Received", "fieldname": "GR Received", "fieldtype": "Data", "width": 110},
        {"label": "Job work Send Qty", "fieldname": "Job work Send Qty", "fieldtype": "Data", "width": 140},
    ]
    return columns[: glue_idx + 1] + new_columns + columns[glue_idx + 1 :]


def _movement_key(container_no, item_code, lot_no, cone):
    """Item code casing drifts between sources on this bench — the same
    physical item is '50D/8F' on Batch and '50D/8f' on Batch Items, the same
    pattern MI1-I132 hit on Delivery Note batches. Upper-cased so a
    Container Inward row and its Batch always land in the same bucket."""
    return (container_no or "", (item_code or "").upper(), lot_no or "", cint(cone))


MOVEMENT_CACHE_TTL = 6 * 3600
MOVEMENT_CACHE_KEY = "mhr:balance_report_v2:movement_map"
MOVEMENT_WARM_JOB_ID = "mhr::warm_movement_map"


def movement_cache_key():
    """Content address of the whole-site movement map: changes the moment a
    relevant Container or Delivery Note is created, edited, submitted or
    cancelled. A child row (Delivery Note Item) is saved as part of its
    parent document, which always bumps the PARENT's own `modified` — so
    tracking the parent doctypes here is sufficient, the same reasoning
    balance_cache_key (the original report) already relies on for its own
    child-table sources.

    MI1-I143: Container is still tracked even though its own Batch Items no
    longer feed anything here (In Qty moved to the Serial and Batch Bundle
    ledger) — Stock Entry (Send to Subcontractor) is the other live source,
    tracked below."""
    container_modified = frappe.db.sql("SELECT MAX(modified) FROM `tabContainer`")[0][0]
    se_modified = frappe.db.sql("SELECT MAX(modified) FROM `tabStock Entry`")[0][0]
    dn_modified = frappe.db.sql("SELECT MAX(modified) FROM `tabDelivery Note`")[0][0]
    return f"{MOVEMENT_CACHE_KEY}:{container_modified}:{se_modified}:{dn_modified}"


def get_movement_totals(container=None, lot_no=None, cone=None):
    """Cached wrapper around _compute_movement_totals for the one call shape
    that is actually expensive: no Container / Lot / Cone filter at all (a
    full unfiltered run of the report). MI1-I135 (2026-09-12): this
    aggregation alone took ~6-7 s unfiltered, on top of the original
    report's own whole-site balance scan — enough to put some unfiltered
    runs of this report over frappe's 15 s prepared-report threshold, and a
    live investigation caught the report getting stuck in background mode
    on roughly half of a handful of unfiltered opens (see REPORTS_TO_KEEP_
    INLINE in mhr.utilis for the exact race). Content-addressed the same
    way get_all_warehouse_balances caches the balance map: any relevant
    document changing the Container / Stock Entry / Delivery Note tables
    gives a new key, so a cached map can never read stale.

    MI1-I143: this now covers ONLY GR Received (returns) and Job work Send
    Qty — In Qty / Out Qty moved to get_ledger_in_out()'s own per-batch,
    real Serial-and-Batch-Bundle read (see that function's docstring for
    why: it must match the existing Container Report exactly).

    A narrow (container/lot/cone-filtered) call is already fast — see the
    docstring below — and is never cached, matching the original report's
    own names-first-vs-whole-site split.
    """
    if container or lot_no or cone:
        return _compute_movement_totals(container, lot_no, cone)
    key = movement_cache_key()
    cached = _original._read_cached_map(key)
    if isinstance(cached, dict):
        return cached
    totals = _compute_movement_totals()
    frappe.cache().set_value(key, totals, expires_in_sec=MOVEMENT_CACHE_TTL)
    return totals


def warm_movement_cache():
    """Build the whole-site movement map if Redis does not hold it yet.
    Runs in the long queue after any Container / Stock Entry / Delivery
    Note submit or cancel (enqueue_movement_cache_warmup) and hourly as a
    safety net, mirroring warm_balance_cache exactly."""
    key = movement_cache_key()
    if isinstance(_original._read_cached_map(key), dict):
        return "warm"
    get_movement_totals()
    return "built"


def enqueue_movement_cache_warmup(doc=None, method=None):
    """doc_events hook (Container / Stock Entry / Delivery Note submit and
    cancel): queue one warm-up after the transaction commits. Deduplicated
    by job id, so a burst of documents in a minute queues one job, and a
    failure to enqueue never blocks the document — mirrors
    enqueue_balance_cache_warmup exactly."""
    try:
        frappe.enqueue(
            "mhr.mhr.report.stock_sheet_(balance_report)_v2.stock_sheet_(balance_report)_v2.warm_movement_cache",
            queue="long",
            job_id=MOVEMENT_WARM_JOB_ID,
            deduplicate=True,
            enqueue_after_commit=True,
        )
    except Exception:
        frappe.log_error(title="Stock Sheet (Balance Report) v2 movement cache warm-up not queued")


def _compute_movement_totals(container=None, lot_no=None, cone=None):
    """(container_no, item_code, lot_no, cone) -> GR Received / Job work
    Send Qty totals (and their "box" row counts). MI1-I143: In Qty / Out
    Qty no longer come from here — see get_ledger_in_out().

    Real schema, not the FRD's placeholder names (its own sheet 1 flagged
    these as needing confirmation): Send to Subcontractor's container/lot
    are its OWN header fields (custom_container_number / custom_lot_no).
    Cone is cint()'d on both sides of every lookup so an Int column here
    can never mismatch a Data one elsewhere.

    container / lot_no / cone narrow both queries the same way the batch
    query narrows Step 1 — a single-container lookup has no reason to
    aggregate the whole site's movement history first.
    """
    totals = {}
    params = {}
    extra = ""
    if container:
        extra += " AND {col_container} = %(container)s"
        params["container"] = container
    if lot_no:
        extra += " AND {col_lot} = %(lot_no)s"
        params["lot_no"] = lot_no
    if cone:
        extra += " AND {col_cone} = %(cone)s"
        params["cone"] = cone

    def _add(rows, gr_qty=0, jw_qty=0):
        for container_no, item_code, l_no, c, qty, box in rows:
            key = _movement_key(container_no, item_code, l_no, c)
            t = totals.setdefault(
                key, {"gr_qty": 0.0, "jw_qty": 0.0, "gr_box": 0, "jw_box": 0}
            )
            q = flt(qty)
            b = cint(box)
            if gr_qty:
                t["gr_qty"] += q
                t["gr_box"] += b
            if jw_qty:
                t["jw_qty"] += q
                t["jw_box"] += b

    # 1 — Sales Return -> GR Received. Return quantities are stored negative
    # (FRD Issue 5) — ABS() so a return ADDS to the balance rather than
    # subtracting a second time on top of what Out Qty already excluded.
    _add(
        frappe.db.sql(
            ("""
            SELECT dni.custom_container_no, dni.item_code, dni.custom_lot_no, dni.custom_cone,
                   SUM(ABS(dni.qty)), COUNT(*)
            FROM `tabDelivery Note Item` dni
            JOIN `tabDelivery Note` dn ON dn.name = dni.parent
            WHERE dn.docstatus = 1 AND dn.is_return = 1
            """ + extra + """
            GROUP BY dni.custom_container_no, dni.item_code, dni.custom_lot_no, dni.custom_cone
            """).format(col_container="dni.custom_container_no", col_lot="dni.custom_lot_no", col_cone="dni.custom_cone"),
            params,
        ),
        gr_qty=1,
    )

    # 2 — Send to Subcontractor issue rows -> Job work Send Qty. Container /
    # Lot are the SEND entry's own header fields (the material being sent,
    # not what comes back).
    _add(
        frappe.db.sql(
            ("""
            SELECT se.custom_container_number, sed.item_code, se.custom_lot_no, sed.custom_cone,
                   SUM(sed.qty), COUNT(*)
            FROM `tabStock Entry Detail` sed
            JOIN `tabStock Entry` se ON se.name = sed.parent
            WHERE se.docstatus = 1
              AND se.stock_entry_type = 'Send to Subcontractor'
              AND IFNULL(sed.s_warehouse, '') != ''
              AND IFNULL(se.custom_container_number, '') != ''
            """ + extra + """
            GROUP BY se.custom_container_number, sed.item_code, se.custom_lot_no, sed.custom_cone
            """).format(col_container="se.custom_container_number", col_lot="se.custom_lot_no", col_cone="sed.custom_cone"),
            params,
        ),
        jw_qty=1,
    )

    return totals


LEDGER_CHUNK = 2000


def get_ledger_in_out(batch_ids):
    """MI1-I143 (2026-09-15, live review with Raj): In Qty / Out Qty match
    the existing "Container Report" (mhr/mhr/report/container_report)
    exactly — summed from the real Serial and Batch Bundle ledger (Serial
    and Batch Entry rows), not from Batch Items / Delivery Note Item /
    Stock Entry Detail. Returns {batch_id: {"in_qty", "out_qty", "in_box",
    "out_box"}}; a batch with no ledger history at all is simply absent.

    In Qty EXCLUDES a Delivery Note return's own Inward-tagged bundle.
    ERPNext tags ANY stock movement back into a warehouse "Inward" purely
    from the posting Stock Ledger Entry's actual_qty sign
    (erpnext/stock/serial_batch_bundle.py) — there is no separate "return"
    classification — so a return posts an Inward bundle on the SAME batch
    it was shipped from (confirmed on real data: MAT-DN-RET-2026-00014's
    bundle reads type_of_transaction=Inward, same batch_no as the original
    shipment). Counting it here AND in GR Received (which reads returns
    from Delivery Note Item directly) would double it in the Balance Qty
    formula. `voucher_type != 'Delivery Note'` excludes every return-
    sourced Inward bundle; real Container Inward posts through Purchase
    Receipt and Job Work Received through Stock Entry, so nothing real is
    excluded by this filter. Out Qty needs no equivalent filter — a normal
    (non-return) delivery is the only Outward-tagged source in this domain.
    """
    out = {}
    if not batch_ids:
        return out
    for i in range(0, len(batch_ids), LEDGER_CHUNK):
        chunk = tuple(batch_ids[i : i + LEDGER_CHUNK])
        rows = frappe.db.sql(
            """
            SELECT
                sbe.batch_no,
                SUM(CASE WHEN sbb.type_of_transaction = 'Inward'
                          AND sbb.voucher_type != 'Delivery Note'
                     THEN ABS(sbe.qty) ELSE 0 END) AS in_qty,
                SUM(CASE WHEN sbb.type_of_transaction = 'Inward'
                          AND sbb.voucher_type != 'Delivery Note'
                     THEN 1 ELSE 0 END) AS in_box,
                SUM(CASE WHEN sbb.type_of_transaction = 'Outward'
                     THEN ABS(sbe.qty) ELSE 0 END) AS out_qty,
                SUM(CASE WHEN sbb.type_of_transaction = 'Outward'
                     THEN 1 ELSE 0 END) AS out_box
            FROM `tabSerial and Batch Entry` sbe
            INNER JOIN `tabSerial and Batch Bundle` sbb
                ON sbb.name = sbe.parent AND sbb.docstatus = 1
            WHERE sbe.batch_no IN %(batch_ids)s
              AND sbb.type_of_transaction IN ('Inward', 'Outward')
            GROUP BY sbe.batch_no
            """,
            {"batch_ids": chunk},
            as_dict=True,
        )
        for r in rows:
            out[r.batch_no] = {
                "in_qty": flt(r.in_qty), "out_qty": flt(r.out_qty),
                "in_box": cint(r.in_box), "out_box": cint(r.out_box),
            }
    return out


LEDGER_CACHE_TTL = 6 * 3600
LEDGER_CACHE_KEY = "mhr:balance_report_v2:ledger_in_out"
LEDGER_WARM_JOB_ID = "mhr::warm_ledger_map"


def _all_ledger_in_out_uncached():
    """One whole-table pass, not `batch_no IN (...)` chunked ~190 times over
    every batch on the site — the same "narrow chunks vs. one full scan"
    switch `get_batch_warehouse_balances` already makes for its own large-set
    case (prod: 0.16 s once vs. 0.02 s x 100 chunks there). Same WHERE/CASE
    logic as get_ledger_in_out(), just without the batch_no filter."""
    rows = frappe.db.sql(
        """
        SELECT
            sbe.batch_no,
            SUM(CASE WHEN sbb.type_of_transaction = 'Inward'
                      AND sbb.voucher_type != 'Delivery Note'
                 THEN ABS(sbe.qty) ELSE 0 END) AS in_qty,
            SUM(CASE WHEN sbb.type_of_transaction = 'Inward'
                      AND sbb.voucher_type != 'Delivery Note'
                 THEN 1 ELSE 0 END) AS in_box,
            SUM(CASE WHEN sbb.type_of_transaction = 'Outward'
                 THEN ABS(sbe.qty) ELSE 0 END) AS out_qty,
            SUM(CASE WHEN sbb.type_of_transaction = 'Outward'
                 THEN 1 ELSE 0 END) AS out_box
        FROM `tabSerial and Batch Entry` sbe
        INNER JOIN `tabSerial and Batch Bundle` sbb
            ON sbb.name = sbe.parent AND sbb.docstatus = 1
        WHERE sbb.type_of_transaction IN ('Inward', 'Outward')
        GROUP BY sbe.batch_no
        """,
        as_dict=True,
    )
    return {
        r.batch_no: {
            "in_qty": flt(r.in_qty), "out_qty": flt(r.out_qty),
            "in_box": cint(r.in_box), "out_box": cint(r.out_box),
        }
        for r in rows
    }


def get_all_ledger_in_out():
    """Whole-site In Qty / Out Qty ledger map, cached the same way
    `get_all_warehouse_balances` caches the balance map (MI1-I143 follow-up,
    2026-09-15, "why is this report taking so long ... system slow" —
    get_ledger_in_out() is a real per-batch DB query with no memoisation of
    its own, so a whole-site run recomputed it fresh every time: ~9 s
    measured against ~79K stocked batches, on top of everything else the
    whole-site path already does, with nothing to show for a repeat run
    within the same stock state — unlike get_movement_totals() right next
    to it, which has had exactly this kind of whole-site Redis cache since
    MI1-I135.

    Content-addressed via the ORIGINAL report's own `balance_cache_key()` —
    it already tracks precisely the two tables this reads (Serial and Batch
    Bundle, Stock Ledger Entry), so the same invalidation rule is correct
    here too — under its own namespaced Redis key so it can never collide
    with the balance map's own cached value under that same content address.
    """
    cached = getattr(frappe.local, "_mhr_v2_all_ledger_in_out", None)
    if cached is not None:
        return cached
    key = f"{LEDGER_CACHE_KEY}:{_original.balance_cache_key()}"
    out = _original._read_cached_map(key)
    if not isinstance(out, dict):
        out = _all_ledger_in_out_uncached()
        frappe.cache().set_value(key, out, expires_in_sec=LEDGER_CACHE_TTL)
    frappe.local._mhr_v2_all_ledger_in_out = out
    return out


def warm_ledger_cache():
    """Build the whole-site ledger map if Redis does not hold it yet. Runs
    in the long queue after any stock movement (enqueue_ledger_cache_warmup)
    and hourly as a safety net, mirroring warm_balance_cache /
    warm_movement_cache exactly."""
    key = f"{LEDGER_CACHE_KEY}:{_original.balance_cache_key()}"
    if isinstance(_original._read_cached_map(key), dict):
        return "warm"
    frappe.local._mhr_v2_all_ledger_in_out = None
    get_all_ledger_in_out()
    return "built"


def enqueue_ledger_cache_warmup(doc=None, method=None):
    """doc_events hook: queue one warm-up after the transaction commits.
    Deduplicated by job id, mirrors enqueue_balance_cache_warmup /
    enqueue_movement_cache_warmup exactly. Wired onto the same doctypes as
    the ORIGINAL report's own enqueue_balance_cache_warmup — Delivery Note,
    Stock Entry, Purchase Receipt, Stock Reconciliation — since both caches
    read the identical underlying tables (Serial and Batch Bundle / Stock
    Ledger Entry) and share the same invalidation key."""
    try:
        frappe.enqueue(
            "mhr.mhr.report.stock_sheet_(balance_report)_v2.stock_sheet_(balance_report)_v2.warm_ledger_cache",
            queue="long",
            job_id=LEDGER_WARM_JOB_ID,
            deduplicate=True,
            enqueue_after_commit=True,
        )
    except Exception:
        frappe.log_error(title="Stock Sheet (Balance Report) v2 ledger cache warm-up not queued")


def get_data(filters=None):
    """MI1-I143 (2026-09-15, live review with Raj — two passes on the same
    day). Row grain: one row per (Container, Item, Lot, Cone, Pulp, Lusture,
    Glue, Grade) — the original report's own grain — instead of MI1-I136's
    collapsed one row per (Container, Lot). Column sources, confirmed on a
    live walkthrough with Raj:

      1. In Qty / Out Qty: computed exactly like the existing "Container
         Report" — the real Serial and Batch Bundle ledger, per batch (see
         get_ledger_in_out()), NOT Container.total_net_weight (MI1-I136)
         and NOT the raw Batch Items / Delivery Note Item child tables
         (MI1-I135's original design). In Qty excludes return-driven Inward
         bundles (see get_ledger_in_out()'s own docstring) so it never
         double-counts against GR Received below.
      2. GR Received / Job work Send Qty: unchanged from MI1-I135/136 — the
         movement ledger (get_movement_totals), returns and Send to
         Subcontractor respectively.
      3. Balance Qty (and Balance Box): In Qty − Out Qty + GR Received −
         Job work Send Qty — the ORIGINAL MI1-I135/136 formula, confirmed
         again explicitly by Raj (not the non-v2 report's live-balance
         approach).
      4. Booked / Delivered / Pending Qty: still the RAW, un-reduced Sales
         Order figures (sales_order_booking_state's own ordered_qty /
         delivered_qty / pending_qty) — matches Raj's description exactly
         (Booked = that Sales Order's own total qty; Pending = Booked −
         Delivered) and was already correct before this ticket.

    Live Serial-and-Batch-Bundle balance (balance_map) is still read here,
    same as always, but for exactly one thing: which of a group's batches
    currently hold stock, to resolve Accepted Warehouse — it does not feed
    Balance Qty.
    """
    if not filters:
        filters = {}

    fdt = filters.get("fdt")
    tdt = filters.get("tdt")
    container = filters.get("container")
    lot_no = filters.get("lot_no")
    cone = filters.get("cone")
    company = filters.get("company")
    transaction_type = filters.get("transaction_type")

    movement_totals = get_movement_totals(container=container, lot_no=lot_no, cone=cone)

    Batch = frappe.qb.DocType("Batch")
    conditions = []

    if fdt:
        conditions.append(Batch.creation >= fdt)
    if tdt:
        conditions.append(Batch.creation < add_days(getdate(tdt), 1))
    if container:
        conditions.append(Batch.custom_container_no == container)
    if lot_no:
        conditions.append(Batch.custom_lot_no == lot_no)
    if cone:
        conditions.append(Batch.custom_cone == cone)

    allowed_containers = None

    if company:
        Container = frappe.qb.DocType("Container")
        company_containers = (
            frappe.qb.from_(Container)
            .select(Container.container_no)
            .where(Container.docstatus == 1)
            .where(Container.company == company)
        ).run(pluck="container_no")
        allowed_containers = {c for c in company_containers if c}

    if transaction_type:
        from mhr.utilis import get_container_nos_by_transaction_type

        tt_containers = get_container_nos_by_transaction_type(transaction_type) or set()
        allowed_containers = (
            tt_containers
            if allowed_containers is None
            else (allowed_containers & tt_containers)
        )

    if allowed_containers is not None:
        if not allowed_containers:
            return []
        conditions.append(Batch.custom_container_no.isin(list(allowed_containers)))

    def _batch_query(*select):
        q = frappe.qb.from_(Batch).select(*select)
        for c in conditions:
            q = q.where(c)
        return q

    from frappe.query_builder.functions import Count

    filtered_count = _batch_query(Count(Batch.name).as_("n")).run(pluck="n")[0]
    query = _batch_query(Batch.name.as_("batch_id"))
    if container or lot_no or cone or filtered_count < WHOLE_SITE_FROM:
        # A narrow set: name it, then aggregate only its batches. Every
        # matching batch is loaded (not just live-stocked ones) — Balance
        # Qty is the ledger formula again, so a fully-shipped batch with a
        # real movement history still needs to render (and net to zero on
        # its own, via Out Qty).
        batch_ids = query.run(pluck="batch_id")
        if not batch_ids:
            return []
        warehouse_balances = get_batch_warehouse_balances(batch_ids)
        balance_map = get_batch_balances(batch_ids, warehouse_balances)
        batches = get_batch_rows(batch_ids)
        stocked_ids = batch_ids
        is_whole_site = False
    else:
        # Dates, Company and Transaction Type alone: the whole site's
        # (cached) balance map names the ~80K stocked batches; a batch with
        # ledger history but zero CURRENT live balance is not in this set
        # (an accepted, pre-existing limitation of the whole-site path —
        # see the module header) — a Container/Lot filter always gets the
        # complete, ungated answer above.
        warehouse_balances = get_all_warehouse_balances()
        balance_map = get_batch_balances(list(warehouse_balances), warehouse_balances)
        stocked_all = [b for b, q in balance_map.items() if flt(q) > 0]
        batches = get_batch_rows(stocked_all)
        lo = None
        hi = None
        if fdt:
            from datetime import datetime
            lo = datetime.combine(getdate(fdt), datetime.min.time())
        if tdt:
            from datetime import datetime
            hi = datetime.combine(add_days(getdate(tdt), 1), datetime.min.time())
        batches = [
            b for b in batches
            if (lo is None or b.creation >= lo)
            and (hi is None or b.creation < hi)
            and (allowed_containers is None or (b.container_no or "") in allowed_containers)
        ]
        stocked_ids = [b.batch_id for b in batches]
        is_whole_site = True
    stocked_by_batch = live_warehouses_by_batch(warehouse_balances)
    if not batches:
        return []
    # Primary-key order, as the single Batch query used to return them: the
    # first batch of a group decides its Merge No, and rows with equal sort
    # keys keep their insertion order.
    batches.sort(key=lambda b: b.batch_id)

    # MI1-I143 follow-up ("why is this report so slow", 2026-09-15):
    # get_ledger_in_out() is a real per-batch DB query with no memoisation
    # of its own — a whole-site run recomputed it fresh every time (~5-9 s
    # measured against 79K stocked batches, on top of everything else the
    # whole-site path already does), unlike get_movement_totals() right
    # next to it, which has had a whole-site Redis cache since MI1-I135.
    # The whole-site case now reads the SAME cached map get_all_ledger_
    # in_out() builds once per stock-state change; a Container/Lot/Cone-
    # filtered run's batch set is small, so it stays a direct, uncached call.
    ledger = get_all_ledger_in_out() if is_whole_site else get_ledger_in_out([b.batch_id for b in batches])

    container_keys = set()
    for b in batches:
        container_keys.add((b.container_no or "", b.lot_no or ""))
    container_info = {}
    if container_keys:
        Container = frappe.qb.DocType("Container")
        cont_nos = list(set(ck[0] for ck in container_keys if ck[0]))
        if cont_nos:
            cont_rows = (
                frappe.qb.from_(Container)
                .select(
                    Container.container_no,
                    Container.lot_no,
                    Container.cross_section,
                    Container.notes,
                    Container.warehouse,
                    Container.production_date,
                    Container.set_warehouse,
                )
                .where(Container.docstatus == 1)
                .where(Container.container_no.isin(cont_nos))
            ).run(as_dict=True)
            for cr in cont_rows:
                ck = (cr.container_no or "", cr.lot_no or "")
                if ck not in container_keys:
                    continue
                # (container_no, lot_no) is NOT a unique key on Container —
                # real data on this bench has dozens of separate Container
                # documents sharing the same container_no+lot_no text (e.g.
                # MCJC-1038 / 14042025). Pre-existing "last one wins"
                # ambiguity for these fields, unrelated to Balance Qty
                # (which no longer reads anything off Container at all).
                existing = container_info.get(ck, {})
                container_info[ck] = {
                    "cross_section": cr.cross_section or existing.get("cross_section", ""),
                    "notes": cr.notes or existing.get("notes", ""),
                    "location": cr.warehouse or existing.get("location", ""),
                    "production_date": str(cr.production_date) if cr.production_date else existing.get("production_date", ""),
                    "accepted_warehouse": cr.set_warehouse or existing.get("accepted_warehouse", ""),
                }

    # MI1-I136 (kept): raw, un-reduced Sales Order figures per batch,
    # independent of the original report's own "effective booking" release
    # logic.
    from mhr.utilis import sales_order_booking_state

    so_state = {}
    batch_to_sos = defaultdict(set)
    CHUNK = 2000
    for i in range(0, len(stocked_ids), CHUNK):
        so_state.update(sales_order_booking_state(batch_names=stocked_ids[i : i + CHUNK]))
    for so, st in so_state.items():
        for r in st["rows"]:
            bid = r.get("batch")
            if bid:
                batch_to_sos[bid].add(so)

    groups = {}
    for b in batches:
        batch_date = getdate(b.creation)
        key = (
            batch_date,
            b.container_no or "",
            b.lot_no or "",
            b.cone or "",
            b.item or "",
            b.pulp or "",
            b.lusture or "",
            b.glue or "",
            b.grade or "",
        )

        if key not in groups:
            ci = container_info.get((b.container_no or "", b.lot_no or ""), {})
            mv = movement_totals.get(
                _movement_key(b.container_no, b.item, b.lot_no, b.cone),
                {"gr_qty": 0.0, "jw_qty": 0.0, "gr_box": 0, "jw_box": 0},
            )
            groups[key] = {
                "batch_date": batch_date,
                "container_no": b.container_no or "",
                "item": b.item or "",
                "pulp": b.pulp or "",
                "lusture": b.lusture or "",
                "glue": b.glue or "",
                "grade": b.grade or "",
                "lot_no": b.lot_no or "",
                "cone": b.cone or "",
                # MI1-I143: In Qty / Out Qty accumulate below, per batch,
                # from the real ledger. GR / Job Work Send captured once
                # here (movement_totals is already keyed at this group's
                # granularity, not per individual batch).
                "in_qty": 0.0, "out_qty": 0.0, "in_box": 0, "out_box": 0,
                "gr_qty": round(mv["gr_qty"], 2), "jw_qty": round(mv["jw_qty"], 2),
                "gr_box": mv["gr_box"], "jw_box": mv["jw_box"],
                "balance": 0.0,
                "balance_box": 0,
                "booked_qty": 0.0,
                "bookings": [],
                "merge_no": b.merge_no or "",
                "cross_section": ci.get("cross_section", ""),
                "production_date": ci.get("production_date", ""),
                "notes": ci.get("notes", ""),
                "location": ci.get("location", ""),
                "accepted_warehouse": ci.get("accepted_warehouse", ""),
                "stocked_batches": [],
                "sales_orders": set(),
            }

        g = groups[key]
        lg = ledger.get(b.batch_id, {"in_qty": 0.0, "out_qty": 0.0, "in_box": 0, "out_box": 0})
        g["in_qty"] += lg["in_qty"]
        g["out_qty"] += lg["out_qty"]
        g["in_box"] += lg["in_box"]
        g["out_box"] += lg["out_box"]

        # Live balance is used ONLY to find which batches are currently
        # stocked, for Accepted Warehouse — not for Balance Qty.
        if flt(balance_map.get(b.batch_id, 0)) > 0:
            g["stocked_batches"].append(b.batch_id)

        g["sales_orders"] |= batch_to_sos.get(b.batch_id, set())

    # MI1-I143: Balance Qty / Balance Box = the ledger formula, now that
    # every group's own in/out/gr/jw pieces are fully accumulated.
    for g in groups.values():
        g["in_qty"] = round(g["in_qty"], 2)
        g["out_qty"] = round(g["out_qty"], 2)
        g["balance"] = round(g["in_qty"] - g["out_qty"] + g["gr_qty"] - g["jw_qty"], 2)
        g["balance_box"] = g["in_box"] - g["out_box"] + g["gr_box"] - g["jw_box"]

    from mhr.utilis import get_container_nos_by_transaction_type

    hty_containers = get_container_nos_by_transaction_type("HTY") or set()

    main_rows = []
    for g in groups.values():
        try:
            cone_num = int(g["cone"]) if g["cone"] else 0
        except (ValueError, TypeError):
            cone_num = 0

        is_hty_row = (g["container_no"] or "") in hty_containers
        if not is_hty_row and cone_num <= 0:
            continue

        # A group only makes the sheet while it is net "in" on the
        # movement ledger (Balance > 0), matching the original v2 design.
        if g["balance_box"] > 0 and flt(g["balance"]) > 0:
            g["sort_order"] = 0
            g["report_date"] = g["batch_date"].strftime("%d/%m/%Y")
            g["accepted_warehouse"] = (
                live_warehouses(g["stocked_batches"], stocked_by_batch) or g["accepted_warehouse"]
            )
            bookings = []
            total_booked = 0.0
            for so in sorted(g["sales_orders"]):
                st = so_state.get(so)
                if not st:
                    continue
                ordered = flt(st["ordered_qty"])
                if ordered <= 0:
                    continue
                bookings.append({
                    "sales_order": so,
                    "booked_qty": round(ordered, 2),
                    "buyer": st.get("customer_name") or "",
                    "sales_person": "",
                    "lifting_terms": st.get("lifting_terms") or "",
                    "delivered_qty": round(flt(st.get("delivered_qty")), 2),
                    "delivered_weight": round(flt(st.get("delivered_weight")), 2),
                    "pending_qty": round(flt(st.get("pending_qty")), 2),
                    "pending_weight": round(flt(st.get("pending_weight")), 2),
                })
                total_booked += ordered
            g["bookings"] = bookings
            g["booked_qty"] = round(total_booked, 2)
            g["available_qty"] = round(flt(g["balance"]) - total_booked, 2)
            g["group_key"] = len(main_rows)
            main_rows.append(g)

    if not main_rows:
        return []

    # Per-lot "Total:" subtotal row (sort_order=1).
    lot_groups = defaultdict(list)
    for row in main_rows:
        lot_key = (row["report_date"], row["container_no"], row["lot_no"])
        lot_groups[lot_key].append(row)

    lot_totals = []
    for (report_date, container_no, lot), rows in lot_groups.items():
        lot_totals.append(
            {
                "batch_date": rows[0]["batch_date"],
                "report_date": report_date,
                "container_no": container_no,
                "item": str(len(rows)),
                "pulp": "", "lusture": "", "glue": "Total:", "grade": "",
                "in_qty": round(sum(r["in_qty"] for r in rows), 2),
                "out_qty": round(sum(r["out_qty"] for r in rows), 2),
                "gr_qty": round(sum(r["gr_qty"] for r in rows), 2),
                "jw_qty": round(sum(r["jw_qty"] for r in rows), 2),
                "balance": round(sum(r["balance"] for r in rows), 2),
                "lot_no": lot,
                "balance_box": sum(r["balance_box"] for r in rows),
                "cone": "",
                "sort_order": 1,
                "booked_qty": round(sum(r["booked_qty"] for r in rows), 2),
                "available_qty": round(sum(r["available_qty"] for r in rows), 2),
                "bookings": [],
                "merge_no": "", "cross_section": "", "production_date": "",
                "notes": "", "location": "", "accepted_warehouse": "",
            }
        )

    container_groups = defaultdict(list)
    for row in main_rows:
        container_groups[row["container_no"]].append(row)

    container_totals = []
    for container_no, rows in container_groups.items():
        lots = set(r["lot_no"] for r in rows)
        if len(lots) > 1:
            container_totals.append(
                {
                    "batch_date": max(r["batch_date"] for r in rows),
                    "report_date": "",
                    "container_no": container_no,
                    "item": str(len(rows)),
                    "pulp": "", "lusture": "", "glue": "Grand Total:", "grade": "",
                    "in_qty": round(sum(r["in_qty"] for r in rows), 2),
                    "out_qty": round(sum(r["out_qty"] for r in rows), 2),
                    "gr_qty": round(sum(r["gr_qty"] for r in rows), 2),
                    "jw_qty": round(sum(r["jw_qty"] for r in rows), 2),
                    "balance": round(sum(r["balance"] for r in rows), 2),
                    "lot_no": "",
                    "balance_box": sum(r["balance_box"] for r in rows),
                    "cone": "",
                    "sort_order": 2,
                    "booked_qty": round(sum(r["booked_qty"] for r in rows), 2),
                    "available_qty": round(sum(r["available_qty"] for r in rows), 2),
                    "bookings": [],
                    "merge_no": "", "cross_section": "", "production_date": "",
                    "notes": "", "location": "", "accepted_warehouse": "",
                }
            )

    all_rows = main_rows + lot_totals + container_totals
    # cone is a real per-row Int, so the original's own cint(cone)
    # tiebreaker applies — otherwise a Chips row (cone 0) and a total row
    # (cone "") can't be ordered against a numbered detail row.
    all_rows.sort(
        key=lambda r: (
            -r["batch_date"].toordinal(),
            r["container_no"],
            r["lot_no"] if r["lot_no"] else "\xff",
            r["sort_order"],
            cint(r["cone"]),
        )
    )

    report_total = {
        "in_qty": round(sum(r["in_qty"] for r in main_rows), 2),
        "out_qty": round(sum(r["out_qty"] for r in main_rows), 2),
        "gr_qty": round(sum(r["gr_qty"] for r in main_rows), 2),
        "jw_qty": round(sum(r["jw_qty"] for r in main_rows), 2),
        "balance": round(sum(r["balance"] for r in main_rows), 2),
        "balance_box": sum(r["balance_box"] for r in main_rows),
        "booked_qty": round(sum(r["booked_qty"] for r in main_rows), 2),
        "available_qty": round(sum(r["available_qty"] for r in main_rows), 2),
        "cone": sum(int(r["cone"]) if r["cone"] else 0 for r in main_rows),
    }

    today_date = getdate(today())

    def _aging_for(row):
        bd = row.get("batch_date")
        if not bd:
            return None
        return date_diff(today_date, bd) or None

    result = []
    for row in all_rows:
        so = row["sort_order"]
        bookings = row.get("bookings", [])

        base = {
            "Date": "" if so >= 1 else row["report_date"],
            "Container Number": "" if so >= 1 else row["container_no"],
            "Item": row["item"],
            "Pulp": strip_prefix(row["pulp"]) if so == 0 else row["pulp"],
            "Lusture": strip_prefix(row["lusture"]) if so == 0 else row["lusture"],
            "Glue": strip_prefix(row["glue"]) if so == 0 else row["glue"],
            "Grade": strip_prefix(row["grade"]) if so == 0 else row["grade"],
            "In Qty": row["in_qty"],
            "Out Qty": row["out_qty"],
            "GR Received": row["gr_qty"],
            "Job work Send Qty": row["jw_qty"],
            "Balance": round(flt(row["balance"]), 2),
            "Lot Number": row["lot_no"],
            "Balance Box": row["balance_box"],
            "Cone": row["cone"],
            "Booked Qty": round(flt(row["booked_qty"]), 2),
            "Available Qty": round(flt(row.get("available_qty", 0)), 2),
            "Merge No": row.get("merge_no", "") if so == 0 else "",
            "Cross Section": row.get("cross_section", "") if so == 0 else "",
            "Production Date": row.get("production_date", "") if so == 0 else "",
            "Notes": row.get("notes", "") if so == 0 else "",
            "Location": row.get("location", "") if so == 0 else "",
            "Accepted Warehouse": row.get("accepted_warehouse", "") if so == 0 else "",
            "Aging": _aging_for(row) if so == 0 else None,
            "sort_order": so,
            "_group_key": row.get("group_key") if so == 0 else None,
        }

        for col in ("Delivered Qty", "Delivered Weight", "Pending Qty", "Pending Weight"):
            base[col] = ""

        if so != 0 or not bookings:
            base["Sales Order"] = ""
            base["Buyers"] = ""
            base["Sales Person"] = ""
            base["Buyer Qty"] = ""
            base["Lifting Terms"] = ""
            result.append(base)
        else:
            balance = flt(row["balance"])
            for bk in bookings:
                bk_qty = flt(bk["booked_qty"])
                so_row = dict(base)
                so_row["Booked Qty"] = round(bk_qty, 2)
                so_row["Buyer Qty"] = round(bk_qty, 2) if bk_qty else ""
                so_row["Available Qty"] = round(balance - bk_qty, 2)
                so_row["Sales Order"] = bk.get("sales_order", "")
                so_row["Buyers"] = bk.get("buyer", "")
                so_row["Sales Person"] = bk.get("sales_person", "")
                so_row["Lifting Terms"] = bk.get("lifting_terms", "")
                so_row["Delivered Qty"] = bk.get("delivered_qty", "")
                so_row["Delivered Weight"] = bk.get("delivered_weight", "")
                so_row["Pending Qty"] = bk.get("pending_qty", "")
                so_row["Pending Weight"] = bk.get("pending_weight", "")
                result.append(so_row)

    result.append({
        "Date": "", "Container Number": "", "Item": "<b>Total</b>",
        "Pulp": "", "Lusture": "", "Glue": "", "Grade": "",
        "In Qty": report_total["in_qty"],
        "Out Qty": report_total["out_qty"],
        "GR Received": report_total["gr_qty"],
        "Job work Send Qty": report_total["jw_qty"],
        "Balance": report_total["balance"],
        "Lot Number": "",
        "Balance Box": report_total["balance_box"],
        "Cone": report_total["cone"],
        "Booked Qty": report_total["booked_qty"],
        "Available Qty": report_total["available_qty"],
        "Sales Order": "", "Buyers": "", "Sales Person": "",
        "Buyer Qty": report_total["booked_qty"],
        "Lifting Terms": "",
        "Delivered Qty": "", "Delivered Weight": "", "Pending Qty": "", "Pending Weight": "",
        "Merge No": "", "Cross Section": "", "Production Date": "", "Notes": "",
        "Location": "", "Accepted Warehouse": "",
        "Aging": None, "sort_order": 3, "_group_key": None,
    })

    return result
