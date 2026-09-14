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
# Three real changes came out of it (see get_data's own docstring for the
# full reasoning): (1) In Qty is now a direct read of Container.
# total_net_weight, never derived from movement rows — the previous
# movement-ledger sum quietly let a LATER transaction (Job Work Received)
# change what should be a fixed Container Inward figure; (2) Booked /
# Delivered / Pending Qty now show the RAW, un-reduced Sales Order figures
# instead of the original report's "effective/released" booking — a
# display choice for this column in this report only, the shared booking
# logic elsewhere in the app is untouched; (3) the row grain itself
# collapsed from one row per (Container, Item, Lot, Cone) to one row per
# (Container, Lot) — In Qty is a single per-container-lot number that
# cannot be meaningfully split across items/cones, so the batch-level
# columns (Item, Cone, Pulp, Lusture, Glue, Grade, Merge No) now show the
# group's distinct values, comma-joined, on that one row instead of being
# broken out across separate rows. The old per-Lot "Total:" subtotal row is
# gone as a result (it would just duplicate the single container+lot row);
# the per-Container "Grand Total:" row (spanning multiple lots) stays.
# (container_no, lot_no) is NOT a unique key on Container -- real data has
# 50+ separate Container documents sharing the same text (e.g. MCJC-1038 /
# 14042025), each its own inward event -- so total_net_weight is SUMMED
# across every document sharing the key, not read from just one.
#
# The generic balance/warehouse primitives (whole-site balance map, Redis
# caching, FORCE INDEX hint, live warehouse resolution) are IMPORTED from
# the original report rather than duplicated — they are unrelated to what
# either change request touches, and any future fix to them (index
# maintenance, a new warehouse rule) should not need to be applied twice.
#
# Known limitation, carried over verbatim from the FRD's own "Issues to
# Settle" sheet (Issue 7): Balance Qty here covers only four movement types
# (Container Inward is now Container.total_net_weight directly rather than a
# movement source, but Job Work Received "produce" rows are consequently no
# longer counted anywhere in this report at all — In Qty's source doctype is
# Container by requirement). Out Qty / GR Received / Job Work Send Qty still
# don't cover Purchase Receipt, Material Issue, Stock Reconciliation,
# transfers between the company's own warehouses, or scrap. If any of those
# occur against VFY/HTY stock, THIS report's Balance Qty can disagree with
# the original Stock Sheet (Balance Report) and with Stock Ledger for the
# same row — that is why the original is left untouched rather than
# modified in place, and why "Accepted Warehouse" (still sourced from the
# live Serial and Batch Bundle balance, unchanged) can show a location even
# on a row whose movement-ledger Balance Qty is very small, zero, or
# disagrees with what is actually in that warehouse.

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
    relevant Container, Stock Entry or Delivery Note is created, edited,
    submitted or cancelled. A child row (Batch Items / Stock Entry Detail /
    Delivery Note Item) is saved as part of its parent document, which
    always bumps the PARENT's own `modified` — so tracking the three parent
    doctypes here is sufficient, the same reasoning balance_cache_key (the
    original report) already relies on for its own child-table sources."""
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
    """(container_no, item_code, lot_no, cone) -> qty/box movement totals,
    one entry per FRD sheet-1 row: In Qty (Container Inward + Job Work
    Received produce rows), Out Qty (non-return deliveries), GR Received
    (return deliveries, ABS'd — return quantities are stored negative), Job
    work Send Qty (Send to Subcontractor issue rows). "Box" is a parallel
    count of the contributing rows in each source, the same shape the FRD's
    Issue 8 suggests for Balance Box.

    Real schema, not the FRD's placeholder names (its own sheet 1 flagged
    these as needing confirmation): this app has no "Container Item" child
    table — a Container's items live in "Batch Items" (qty is what the rest
    of the codebase already treats as net weight, e.g. mhr.utilis's own
    Batch Items reader). Stock Entry Detail carries no container/lot columns
    at all (see the Subcontract receipt flow note in CLAUDE.md) — Send to
    Subcontractor's container/lot are its OWN header fields
    (custom_container_number / custom_lot_no); Job Work Received's are the
    RECEIVED header fields (custom_received_container_no /
    custom_received_lot_no). Cone is cint()'d on both sides of every lookup
    so an Int column here can never mismatch a Data one elsewhere.

    container / lot_no / cone narrow every one of the five queries the same
    way the batch query narrows Step 1 — a single-container lookup has no
    reason to aggregate the whole site's movement history first.
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

    def _add(rows, in_qty=0, out_qty=0, gr_qty=0, jw_qty=0):
        for container_no, item_code, l_no, c, qty, box in rows:
            key = _movement_key(container_no, item_code, l_no, c)
            t = totals.setdefault(
                key, {"in_qty": 0.0, "out_qty": 0.0, "gr_qty": 0.0, "jw_qty": 0.0,
                      "in_box": 0, "out_box": 0, "gr_box": 0, "jw_box": 0}
            )
            q = flt(qty)
            b = cint(box)
            if in_qty:
                t["in_qty"] += q
                t["in_box"] += b
            if out_qty:
                t["out_qty"] += q
                t["out_box"] += b
            if gr_qty:
                t["gr_qty"] += q
                t["gr_box"] += b
            if jw_qty:
                t["jw_qty"] += q
                t["jw_box"] += b

    # 1 — Container Inward -> In Qty. Batch Items is the Container's item
    # child table on this app (there is no "Container Item" doctype).
    _add(
        frappe.db.sql(
            ("""
            SELECT c.container_no, bi.item, c.lot_no, bi.cone, SUM(bi.qty), COUNT(*)
            FROM `tabBatch Items` bi
            JOIN `tabContainer` c ON c.name = bi.parent AND bi.parenttype = 'Container'
            WHERE c.docstatus = 1
            """ + extra + """
            GROUP BY c.container_no, bi.item, c.lot_no, bi.cone
            """).format(col_container="c.container_no", col_lot="c.lot_no", col_cone="bi.cone"),
            params,
        ),
        in_qty=1,
    )

    # 2 — Job Work Received produce rows -> In Qty. "Produce" = a target
    # warehouse and no source warehouse (FRD Issue 4: there is no clean
    # received-quantity field on the entry itself — Total Qty mixes consume
    # and produce rows, and Received Total Qty is a live balance, not a
    # received amount). Container/Lot come from the RECEIVED header fields;
    # Stock Entry Detail has none of its own.
    _add(
        frappe.db.sql(
            ("""
            SELECT se.custom_received_container_no, sed.item_code, se.custom_received_lot_no,
                   sed.custom_cone, SUM(sed.qty), COUNT(*)
            FROM `tabStock Entry` se
            JOIN `tabStock Entry Detail` sed ON sed.parent = se.name
            WHERE se.docstatus = 1
              AND se.stock_entry_type = 'Job Work Received'
              AND IFNULL(sed.t_warehouse, '') != ''
              AND IFNULL(sed.s_warehouse, '') = ''
              AND IFNULL(se.custom_received_container_no, '') != ''
            """ + extra + """
            GROUP BY se.custom_received_container_no, sed.item_code, se.custom_received_lot_no, sed.custom_cone
            """).format(
                col_container="se.custom_received_container_no",
                col_lot="se.custom_received_lot_no",
                col_cone="sed.custom_cone",
            ),
            params,
        ),
        in_qty=1,
    )

    # 3 — Delivery (non-return) -> Out Qty.
    _add(
        frappe.db.sql(
            ("""
            SELECT dni.custom_container_no, dni.item_code, dni.custom_lot_no, dni.custom_cone,
                   SUM(dni.qty), COUNT(*)
            FROM `tabDelivery Note Item` dni
            JOIN `tabDelivery Note` dn ON dn.name = dni.parent
            WHERE dn.docstatus = 1 AND dn.is_return = 0
            """ + extra + """
            GROUP BY dni.custom_container_no, dni.item_code, dni.custom_lot_no, dni.custom_cone
            """).format(col_container="dni.custom_container_no", col_lot="dni.custom_lot_no", col_cone="dni.custom_cone"),
            params,
        ),
        out_qty=1,
    )

    # 4 — Sales Return -> GR Received. Return quantities are stored negative
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

    # 5 — Send to Subcontractor issue rows -> Job work Send Qty. Container /
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


def get_data(filters=None):
    """MI1-I136 (Change Request, 2026-09-13): one row per (Container, Lot)
    instead of per (Container, Item, Lot, Cone) — the batch/item/cone
    breakdown is consolidated onto that single row (distinct values,
    comma-joined) rather than shown as separate rows. Reworks four of the
    nine columns the change request specifies; everything else (whole-site
    vs narrow batch path, Accepted Warehouse resolution, HTY/VFY column
    swap, filters) is unchanged from the original get_data this was forked
    from.

      1. In Qty is now `Container.total_net_weight` — read once, directly,
         never derived from movement rows, so a later Delivery Note / Sales
         Return / Stock Entry / Sales Order can never change it (the
         change request's core complaint: "later transactions must never
         change In Qty" — the previous movement-ledger sum quietly folded
         in Job Work Received "produce" rows, a later transaction, on top
         of the original Container Inward figure).
      2. Out Qty / GR Received / Job Work Send Qty stay sourced from the
         movement ledger (get_movement_totals), same queries as before,
         now aggregated across every item/cone sharing a (Container, Lot)
         rather than kept separate per item/cone.
      3. Booked / Delivered / Pending Qty read the RAW, un-reduced Sales
         Order figures (sales_order_booking_state's own ordered_qty /
         delivered_qty / pending_qty) instead of the "effective" released
         booking the original report's own booking machinery computes for
         live availability decisions. This is a display-only choice for
         THIS column in THIS report — the shared booking logic itself
         (Sales Order form, lot pickers, the original balance report) is
         untouched.
      4. Balance Qty / Available Qty formulas are unchanged (In - Out + GR
         - JW Send; Balance - Booked) — only what feeds them changed.
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
    movement_by_lot = _aggregate_movements_by_lot(movement_totals)

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
        batch_ids = query.run(pluck="batch_id")
        if not batch_ids:
            return []
        warehouse_balances = get_batch_warehouse_balances(batch_ids)
        balance_map = get_batch_balances(batch_ids, warehouse_balances)
        batches = get_batch_rows(batch_ids)
        stocked_ids = batch_ids
    else:
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
    stocked_by_batch = live_warehouses_by_batch(warehouse_balances)
    if not batches:
        return []
    batches.sort(key=lambda b: b.batch_id)

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
                    Container.total_net_weight,
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
                # MCJC-1038 / 14042025, one small inward event per document).
                # The original report's own container_info lookup already
                # has this same "last one wins" ambiguity for cross_section
                # / notes / location / production_date / accepted_warehouse
                # (pre-existing, untouched, out of scope here) -- but
                # total_net_weight is new to THIS report and is the one
                # figure this whole change request is built around, so
                # "last one wins" would silently drop every other inward
                # event's weight. Summed across every Container document
                # sharing the key instead, consistent with "one consolidated
                # row per (Container, Lot)" covering everything that fed it.
                existing = container_info.get(ck, {})
                container_info[ck] = {
                    "cross_section": cr.cross_section or existing.get("cross_section", ""),
                    "notes": cr.notes or existing.get("notes", ""),
                    "location": cr.warehouse or existing.get("location", ""),
                    "production_date": str(cr.production_date) if cr.production_date else existing.get("production_date", ""),
                    "accepted_warehouse": cr.set_warehouse or existing.get("accepted_warehouse", ""),
                    # Requirement 1: In Qty is a direct read of this field,
                    # never derived from Batch Items / SLE -- summed here
                    # because the key isn't unique (see above).
                    "total_net_weight": flt(cr.total_net_weight) + flt(existing.get("total_net_weight", 0)),
                }

    # Requirement 3: raw (un-reduced) Sales Order figures per batch,
    # independent of the original report's own "effective booking"
    # release logic.
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
        key = (b.container_no or "", b.lot_no or "")

        if key not in groups:
            ci = container_info.get(key, {})
            lot_mv = movement_by_lot.get(key, {
                "out_qty": 0.0, "gr_qty": 0.0, "jw_qty": 0.0,
                "in_box": 0, "out_box": 0, "gr_box": 0, "jw_box": 0,
            })
            in_qty = round(ci.get("total_net_weight", 0.0), 2)
            out_qty = round(lot_mv["out_qty"], 2)
            gr_qty = round(lot_mv["gr_qty"], 2)
            jw_qty = round(lot_mv["jw_qty"], 2)
            groups[key] = {
                "batch_date": batch_date,
                "container_no": key[0],
                "lot_no": key[1],
                "items": set(), "cones": set(), "pulps": set(),
                "lustures": set(), "glues": set(), "grades": set(), "merge_nos": set(),
                "in_qty": in_qty, "out_qty": out_qty, "gr_qty": gr_qty, "jw_qty": jw_qty,
                "balance": round(in_qty - out_qty + gr_qty - jw_qty, 2),
                "balance_box": lot_mv["in_box"] - lot_mv["out_box"] + lot_mv["gr_box"] - lot_mv["jw_box"],
                "sales_orders": set(),
                "cross_section": ci.get("cross_section", ""),
                "production_date": ci.get("production_date", ""),
                "notes": ci.get("notes", ""),
                "location": ci.get("location", ""),
                "accepted_warehouse": ci.get("accepted_warehouse", ""),
                "stocked_batches": [],
            }
        else:
            batch_date = min(batch_date, groups[key]["batch_date"])
            groups[key]["batch_date"] = batch_date

        g = groups[key]
        if b.item:
            g["items"].add(b.item)
        if b.cone not in (None, ""):
            g["cones"].add(cint(b.cone))
        if b.pulp:
            g["pulps"].add(strip_prefix(b.pulp))
        if b.lusture:
            g["lustures"].add(strip_prefix(b.lusture))
        if b.glue:
            g["glues"].add(strip_prefix(b.glue))
        if b.grade:
            g["grades"].add(strip_prefix(b.grade))
        if b.merge_no:
            g["merge_nos"].add(b.merge_no)

        if flt(balance_map.get(b.batch_id, 0)) > 0:
            g["stocked_batches"].append(b.batch_id)

        g["sales_orders"] |= batch_to_sos.get(b.batch_id, set())

    from mhr.utilis import get_container_nos_by_transaction_type

    hty_containers = get_container_nos_by_transaction_type("HTY") or set()

    def _joined(values):
        return ", ".join(sorted(str(v) for v in values if v not in (None, "")))

    main_rows = []
    for g in groups.values():
        is_hty_row = (g["container_no"] or "") in hty_containers
        has_positive_cone = any(c > 0 for c in g["cones"])
        if not is_hty_row and not has_positive_cone:
            continue

        # Same stocked-only gate as the original / round-1 v2: a group
        # only makes the sheet while it is net "in" on the movement ledger.
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

    all_rows = main_rows + container_totals
    all_rows.sort(
        key=lambda r: (
            -r["batch_date"].toordinal(),
            r["container_no"],
            r["lot_no"] if r["lot_no"] else "\xff",
            r["sort_order"],
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

        if so == 0:
            item_str = _joined(row["items"])
            cone_str = _joined(sorted(row["cones"]))
            pulp_str = _joined(row["pulps"])
            lusture_str = _joined(row["lustures"])
            glue_str = _joined(row["glues"])
            grade_str = _joined(row["grades"])
            merge_no_str = _joined(row["merge_nos"])
        else:
            item_str = row["item"]
            cone_str = row["cone"]
            pulp_str = row["pulp"]
            lusture_str = row["lusture"]
            glue_str = row["glue"]
            grade_str = row["grade"]
            merge_no_str = row.get("merge_no", "")

        base = {
            "Date": "" if so >= 1 else row["report_date"],
            "Container Number": "" if so >= 1 else row["container_no"],
            "Item": item_str,
            "Pulp": pulp_str,
            "Lusture": lusture_str,
            "Glue": glue_str,
            "Grade": grade_str,
            "In Qty": row["in_qty"],
            "Out Qty": row["out_qty"],
            "GR Received": row["gr_qty"],
            "Job work Send Qty": row["jw_qty"],
            "Balance": round(flt(row["balance"]), 2),
            "Lot Number": row["lot_no"],
            "Balance Box": row["balance_box"],
            "Cone": cone_str,
            "Booked Qty": round(flt(row["booked_qty"]), 2),
            "Available Qty": round(flt(row.get("available_qty", 0)), 2),
            "Merge No": merge_no_str,
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
        "Cone": "",
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


def _aggregate_movements_by_lot(movement_totals):
    """Collapse the (container, item, lot, cone)-keyed movement map down to
    (container, lot), summing Out / GR / Job Work Send (and their box
    counts) across every item/cone sharing that container+lot. In Qty is
    NOT summed here — Requirement 1 reads it directly from
    Container.total_net_weight instead, independent of this map."""
    by_lot = {}
    for (container_no, item_code, lot_no, cone), mv in movement_totals.items():
        key = (container_no, lot_no)
        t = by_lot.setdefault(
            key, {"out_qty": 0.0, "gr_qty": 0.0, "jw_qty": 0.0,
                  "in_box": 0, "out_box": 0, "gr_box": 0, "jw_box": 0}
        )
        t["out_qty"] += mv["out_qty"]
        t["gr_qty"] += mv["gr_qty"]
        t["jw_qty"] += mv["jw_qty"]
        t["in_box"] += mv["in_box"]
        t["out_box"] += mv["out_box"]
        t["gr_box"] += mv["gr_box"]
        t["jw_box"] += mv["jw_box"]
    return by_lot
