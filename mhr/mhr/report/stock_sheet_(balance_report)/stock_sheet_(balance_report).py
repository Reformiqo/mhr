# Copyright (c) 2025, reformiqo and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import add_days, cint, date_diff, flt, getdate, today
from frappe.query_builder.functions import Sum
from collections import defaultdict
from datetime import datetime


REPORT_NAME = "STOCK SHEET (BALANCE REPORT)"


def execute(filters=None):
    filters = filters or {}
    started_inline = _runs_inline()
    # MI1-I61 (Raj 2026-06-27): scope by 'HTY User' / 'VFY User' role.
    from mhr.utilis import enforce_role_scoped_transaction_type
    filters = enforce_role_scoped_transaction_type(filters)
    # MI1-I64 (rework): pass filters so column labels can swap with Transaction Type.
    columns = get_columns(filters)
    # MI1-I99 (2026-08-17): transaction_type is applied INSIDE get_data now, on
    # the batch query, so the lot / container / grand totals are summed from the
    # filtered set. The old post-aggregate filter_rows_by_transaction_type() call
    # lived here and matched on "Container Number", which every total row leaves
    # blank — it therefore deleted the Grand Total and every subtotal whenever a
    # Transaction Type was selected. The shared helper is untouched; other
    # reports still use it.
    data = get_data(filters)
    keep_inline(started_inline)
    return columns, data


def _runs_inline():
    try:
        return not frappe.db.get_value("Report", REPORT_NAME, "prepared_report")
    except Exception:
        return False


def keep_inline(started_inline):
    """MI1-I119: frappe starts a 15 s timer with every inline run and, if the
    run is still going when it fires, sets Report.prepared_report = 1 in a
    side connection (report.py :: enable_prepared_report). The one run that
    can still take that long here is the cold build of the whole-site balance
    map right after a stock movement, before the background warm-up has
    finished — and that single run then switched every user to "Generate New
    Report" until someone reset the flag (prod, 2026-09-06 and 2026-09-08).
    A report the users want inline puts itself back: if this run started
    inline and the flag flipped while it ran, the flip is undone. A flag that
    was already 1 when the run started is an administrator's choice and is
    left alone."""
    if not started_inline:
        return
    try:
        if frappe.db.get_value("Report", REPORT_NAME, "prepared_report"):
            frappe.db.set_value("Report", REPORT_NAME, "prepared_report", 0, update_modified=False)
    except Exception:
        frappe.log_error(title="Stock Sheet (Balance Report): could not keep the report inline")


def get_columns(filters=None):
    # MI1-I64 (rework): labels swap with Transaction Type filter.
    # HTY -> Type/Product; VFY (or no filter) -> Pulp/Glue. Fieldnames
    # stay capitalised "Pulp"/"Glue" so the row dicts always resolve.
    # MI1-I64 reopen (Raj 2026-06-29): also DROP the Merge No + Cross
    # Section columns entirely when in HTY mode (they're VFY-only).
    filters = filters or {}
    is_hty = (filters.get("transaction_type") == "HTY")
    pulp_label = "Type" if is_hty else "Pulp"
    glue_label = "Product" if is_hty else "Glue"
    columns = [
        # --- Identity ---
        {"label": _("Date"), "fieldname": "Date", "fieldtype": "Data", "width": 100},
        # MI1-I94 (Raj 2026-08-13): Aging = days from batch/container posting
        # date to the day the report is generated. Blank on total rows (they
        # aggregate mixed dates) and blank when the batch is same-day, so the
        # column reads as "how long has this been sitting" rather than a wall
        # of zeros. Sits between Date and Container No (2026-08-18) — it is
        # read together with the date, not with the warehouse columns.
        {"label": _("Aging"), "fieldname": "Aging", "fieldtype": "Int", "width": 80},
        {"label": _("Container No"), "fieldname": "Container Number", "fieldtype": "Data", "width": 130},
        {"label": _("Item"), "fieldname": "Item", "fieldtype": "Data", "width": 120},
        {"label": _("Lot Number"), "fieldname": "Lot Number", "fieldtype": "Data", "width": 110},
        {"label": _("Grade"), "fieldname": "Grade", "fieldtype": "Data", "width": 90},
    ]
    # MI1-I97 (2026-08-17): surface Colour in HTY mode, right after Grade.
    #
    # No new data is needed — create_batches() already folds the HTY specs into
    # the canonical Batch columns:
    #     Container.product -> Batch.custom_glue
    #     Container.colour  -> Batch.custom_lusture     <-- Colour lives here
    #     Container.type    -> Batch.custom_pulp
    # so `Lusture` has carried the Colour value for HTY all along (verified:
    # MCCA-108 batches hold custom_lusture = "Colour-RAW WHITE", which
    # strip_prefix renders as "RAW WHITE"). MI1-I64 relabelled Pulp -> Type and
    # Glue -> Product for HTY but missed this third pair, so the value was
    # showing under a "Lusture" heading.
    #
    # In HTY the field is emitted once, here, as "Colour". In VFY it stays in
    # its original slot below with its original label — that column list is
    # byte-identical to before.
    if is_hty:
        columns.append({"label": _("Colour"), "fieldname": "Lusture", "fieldtype": "Data", "width": 110})
    columns.append({"label": _("Cone"), "fieldname": "Cone", "fieldtype": "Data", "width": 70})
    if not is_hty:
        columns.append({"label": _("Merge No"), "fieldname": "Merge No", "fieldtype": "Data", "width": 100})
    columns += [
        # --- Specifications ---
        {"label": _(pulp_label), "fieldname": "Pulp", "fieldtype": "Data", "width": 90},
    ]
    if not is_hty:
        columns.append({"label": _("Lusture"), "fieldname": "Lusture", "fieldtype": "Data", "width": 90})
    columns += [
        {"label": _(glue_label), "fieldname": "Glue", "fieldtype": "Data", "width": 90},
        # --- Stock ---
        {"label": _("Balance Qty"), "fieldname": "Balance", "fieldtype": "Data", "width": 110},
        {"label": _("Booked Qty"), "fieldname": "Buyer Qty", "fieldtype": "Data", "width": 100},
        {"label": _("Balance Box"), "fieldname": "Balance Box", "fieldtype": "Data", "width": 100},
        {"label": _("Total Booked"), "fieldname": "Booked Qty", "fieldtype": "Data", "width": 110},
        {"label": _("Available Qty"), "fieldname": "Available Qty", "fieldtype": "Data", "width": 110},
        # --- Booking Details ---
        {"label": _("Sales Order"), "fieldname": "Sales Order", "fieldtype": "Link", "options": "Sales Order", "width": 150},
        {"label": _("Buyer"), "fieldname": "Buyers", "fieldtype": "Data", "width": 180},
        {"label": _("Sales Person"), "fieldname": "Sales Person", "fieldtype": "Data", "width": 130},
        {"label": _("Lifting Terms"), "fieldname": "Lifting Terms", "fieldtype": "Data", "width": 110},
        # MI1-I120 revision (Raj 2026-09-05): the Sales Order's delivery
        # picture — sums over the submitted notes linked to it, no batch
        # condition — beside its booking.
        {"label": _("Delivered Qty"), "fieldname": "Delivered Qty", "fieldtype": "Data", "width": 110},
        {"label": _("Delivered Weight"), "fieldname": "Delivered Weight", "fieldtype": "Data", "width": 120},
        {"label": _("Pending Qty"), "fieldname": "Pending Qty", "fieldtype": "Data", "width": 110},
        {"label": _("Pending Weight"), "fieldname": "Pending Weight", "fieldtype": "Data", "width": 120},
    ]
    if not is_hty:
        columns.append({"label": _("Cross Section"), "fieldname": "Cross Section", "fieldtype": "Data", "width": 110})
    columns += [
        # --- Additional Info ---
        {"label": _("Production Date"), "fieldname": "Production Date", "fieldtype": "Data", "width": 120},
        {"label": _("Notes"), "fieldname": "Notes", "fieldtype": "Data", "width": 150},
        {"label": _("Location"), "fieldname": "Location", "fieldtype": "Data", "width": 100},
        {"label": _("Accepted Warehouse"), "fieldname": "Accepted Warehouse", "fieldtype": "Data", "width": 150},
        {"label": _("sort_order"), "fieldname": "sort_order", "fieldtype": "Int", "width": 0, "hidden": 1},
    ]
    return columns


def strip_prefix(val):
    """Strip item specification prefix (e.g. 'SPEC-value' -> 'value')"""
    if val and "-" in str(val):
        return str(val).rsplit("-", 1)[-1]
    return val or ""


def get_external_job_work_warehouses():
    """MI1-I82 (Raj 2026-07-16): return the set of Warehouse names
    flagged as External Job Work Warehouses (non-company job-work
    warehouses via the custom_external_job_work_warehouse Check field).

    Stock in these warehouses stays in the system for transactions
    (Stock Entry, Send to Subcontractor, Material Transfer, etc.) but
    must NOT count as company stock in this report — the flag causes
    their SLE / SBE rows to be excluded from the balance aggregation.

    Returns an empty set when the custom field hasn't been installed
    yet (pre-migration state) so this remains safe on old fixtures.
    """
    if not frappe.db.has_column("Warehouse", "custom_external_job_work_warehouse"):
        return set()
    return set(frappe.db.sql_list(
        """SELECT name FROM `tabWarehouse`
           WHERE custom_external_job_work_warehouse = 1"""
    ))


BALANCE_CHUNK = 5000
SBB_STATUS_INDEX = "idx_sbb_name_status"


def _sbb_status_index_hint():
    """FORCE INDEX clause for the bundle join, or '' until the MI1-I119 patch
    has created the index (a hint naming a missing index is a SQL error).

    The optimizer prefers the clustered primary key for `sbb.name = sbe.parent`
    and reads the whole 380K-row bundle table through it, which is what made
    a full-range run spend 73 of 85 seconds in this join. The covering
    `(name, docstatus, is_cancelled)` index answers the same lookup from
    ~12 MB, but MariaDB only picks it when told to. Checked once per request.
    """
    cached = getattr(frappe.local, "_mhr_sbb_status_index", None)
    if cached is None:
        cached = bool(
            frappe.db.sql(
                """SELECT 1 FROM information_schema.statistics
                   WHERE table_schema = DATABASE()
                     AND table_name = 'tabSerial and Batch Bundle'
                     AND index_name = %s
                   LIMIT 1""",
                (SBB_STATUS_INDEX,),
            )
        )
        frappe.local._mhr_sbb_status_index = cached
    return f"FORCE INDEX (`{SBB_STATUS_INDEX}`)" if cached else ""


LEGACY_SLE_WHOLE_TABLE_FROM = 20000
WHOLE_SITE_FROM = 50000
BALANCE_CACHE_TTL = 6 * 3600
BALANCE_CACHE_KEY = "mhr:balance_report:warehouse_balances"
WARM_JOB_ID = "mhr::warm_balance_map"


def get_batch_warehouse_balances(batch_ids):
    """Live stock per (batch, warehouse): {(batch_no, warehouse): qty}.

    One set-based query per chunk over Serial and Batch Entry, keeping only
    rows whose bundle is submitted and not cancelled — the same rule as
    mhr.utilis._batch_balance_in_warehouse, and the only correct one: this
    site holds ~51K entry rows whose bundle was deleted from under them
    (22.6K batches would show phantom stock without the join). Pre-bundle
    Stock Ledger rows that name the batch directly are added on top — per
    chunk for a small set, in ONE whole-table pass for a large one (prod:
    0.16 s once versus 0.02 s x 100 chunks). Zero-sum (batch, warehouse)
    pairs are dropped by the database, so the result is roughly the stocked
    set, not every batch ever moved.

    MI1-I82: External Job Work warehouses are excluded here, so nothing that
    consumes this map ever counts a subcontractor's stock as the company's.
    """
    out = {}
    names = [b for b in batch_ids if b]
    if not names:
        return out
    external_wh = get_external_job_work_warehouses()
    ext_sql = " AND sbe.warehouse NOT IN %(external)s" if external_wh else ""
    ext_sql_sle = " AND warehouse NOT IN %(external)s" if external_wh else ""
    params = {"external": tuple(external_wh)} if external_wh else {}
    hint = _sbb_status_index_hint()
    legacy_per_chunk = len(names) < LEGACY_SLE_WHOLE_TABLE_FROM

    for i in range(0, len(names), BALANCE_CHUNK):
        chunk = tuple(names[i : i + BALANCE_CHUNK])
        # Bundle entries (ERPNext v15+ method).
        for batch_no, warehouse, qty in frappe.db.sql(
            f"""
            SELECT sbe.batch_no, sbe.warehouse, SUM(sbe.qty)
            FROM `tabSerial and Batch Entry` sbe
            INNER JOIN `tabSerial and Batch Bundle` sbb {hint} ON sbb.name = sbe.parent
            WHERE sbb.docstatus = 1 AND sbb.is_cancelled = 0
              AND sbe.batch_no IN %(batches)s{ext_sql}
            GROUP BY sbe.batch_no, sbe.warehouse
            HAVING ABS(SUM(sbe.qty)) > 0.0005
            """,
            {"batches": chunk, **params},
        ):
            key = (batch_no, warehouse or "")
            out[key] = out.get(key, 0.0) + flt(qty)
        if not legacy_per_chunk:
            continue
        # Direct SLE entries (older method — batch_no on the ledger row itself).
        for batch_no, warehouse, qty in frappe.db.sql(
            f"""
            SELECT batch_no, warehouse, SUM(actual_qty)
            FROM `tabStock Ledger Entry`
            WHERE docstatus = 1 AND is_cancelled = 0
              AND batch_no IN %(batches)s
              AND IFNULL(serial_and_batch_bundle, '') = ''{ext_sql_sle}
            GROUP BY batch_no, warehouse
            HAVING ABS(SUM(actual_qty)) > 0.0005
            """,
            {"batches": chunk, **params},
        ):
            key = (batch_no, warehouse or "")
            out[key] = out.get(key, 0.0) + flt(qty)

    if not legacy_per_chunk:
        wanted = set(names)
        for batch_no, warehouse, qty in frappe.db.sql(
            f"""
            SELECT batch_no, warehouse, SUM(actual_qty)
            FROM `tabStock Ledger Entry`
            WHERE docstatus = 1 AND is_cancelled = 0
              AND IFNULL(batch_no, '') != ''
              AND IFNULL(serial_and_batch_bundle, '') = ''{ext_sql_sle}
            GROUP BY batch_no, warehouse
            HAVING ABS(SUM(actual_qty)) > 0.0005
            """,
            params,
        ):
            if batch_no in wanted:
                key = (batch_no, warehouse or "")
                out[key] = out.get(key, 0.0) + flt(qty)
    return out


def balance_cache_key():
    """Content address of the whole-site balance map: it changes the moment a
    bundle or ledger row is written, so a cached map can never be stale — a
    new Delivery Note, Stock Entry or cancellation bumps MAX(modified) (and
    the bundle count), and an External Job Work flag flip changes the set."""
    sbb_count, sbb_modified = frappe.db.sql("SELECT COUNT(*), MAX(modified) FROM `tabSerial and Batch Bundle`")[0]
    sle_modified = frappe.db.sql("SELECT MAX(modified) FROM `tabStock Ledger Entry`")[0][0]
    external = "|".join(sorted(get_external_job_work_warehouses()))
    return f"{BALANCE_CACHE_KEY}:{sbb_count}:{sbb_modified}:{sle_modified}:{external}"


def get_all_warehouse_balances(use_cache=True):
    """The whole site's live (batch, warehouse) balances — what every run
    without a Container / Lot / Cone filter needs, whatever its dates.

    MI1-I119 (prod 2026-09-08): on prod the chunked aggregate over all 506K
    batches is ~10 s and a full-range run landed at 16 s — past the 15 s mark
    at which frappe flips the report back into prepared mode. The map depends
    on nothing but the stock tables, so it is kept in Redis under a key that
    encodes their state (balance_cache_key): any stock movement gives a new
    key, and a repeat run — a date, company or mode change, which is how the
    sheet is actually used — skips the aggregate entirely. This is not the
    "second cache layer" CLAUDE.md warns about (a time-based copy next to
    prepared_report): prepared_report is off for this report, and the key is
    content-addressed, so no user can see a figure that a transaction has
    since changed. One request never computes it twice (frappe.local)."""
    cached = getattr(frappe.local, "_mhr_all_warehouse_balances", None)
    if cached is not None:
        return cached
    key = balance_cache_key() if use_cache else None
    out = _read_cached_map(key) if use_cache else None
    if not isinstance(out, dict):
        names = frappe.db.sql_list("SELECT name FROM `tabBatch`")
        out = get_batch_warehouse_balances(names)
        if use_cache:
            frappe.cache().set_value(key, out, expires_in_sec=BALANCE_CACHE_TTL)
    frappe.local._mhr_all_warehouse_balances = out
    return out


def _read_cached_map(key):
    """Redis read that bypasses frappe.local.cache. RedisWrapper.get_value
    remembers a miss (None) for the rest of the request and set_value with an
    expiry does not refresh that memory, so a build right after a miss would
    read back None in the same process (the warm-up job, and the tests)."""
    import pickle

    cache = frappe.cache()
    try:
        raw = cache.get(cache.make_key(key))
    except Exception:
        return None
    if not raw:
        return None
    try:
        return pickle.loads(raw)
    except Exception:
        return None


def warm_balance_cache():
    """Build the whole-site map for the current stock state if Redis does not
    hold it yet. Runs in the long queue after any stock movement
    (enqueue_balance_cache_warmup) and hourly as a safety net, so the sheet
    almost always finds the map ready and a run stays well inside the 15 s
    after which frappe would flip it back into prepared mode."""
    key = balance_cache_key()
    if isinstance(_read_cached_map(key), dict):
        return "warm"
    frappe.local._mhr_all_warehouse_balances = None
    get_all_warehouse_balances()
    return "built"


def enqueue_balance_cache_warmup(doc=None, method=None):
    """doc_events hook on the stock documents (submit / cancel): queue one
    warm-up after the transaction commits. Deduplicated by job id, so a
    hundred Delivery Notes in a minute queue one job, and a failure to
    enqueue never blocks the document."""
    try:
        frappe.enqueue(
            "mhr.mhr.report.stock_sheet_(balance_report).stock_sheet_(balance_report).warm_balance_cache",
            queue="long",
            job_id=WARM_JOB_ID,
            deduplicate=True,
            enqueue_after_commit=True,
        )
    except Exception:
        frappe.log_error(title="Stock Sheet balance cache warm-up not queued")


def get_batch_balances(batch_ids, warehouse_balances=None):
    """Get stock balance per batch: {batch_no: qty}, summed over warehouses.

    MI1-I82 (Raj 2026-07-16): entries in External Job Work warehouses are
    excluded (inside get_batch_warehouse_balances, which calls
    get_external_job_work_warehouses) so their stock does not count as
    company balance. Pass `warehouse_balances` to reuse a map already built.
    """
    if not batch_ids and warehouse_balances is None:
        return {}
    if warehouse_balances is None:
        warehouse_balances = get_batch_warehouse_balances(batch_ids)
    balance_map = {}
    for (batch_no, _warehouse), qty in warehouse_balances.items():
        balance_map[batch_no] = balance_map.get(batch_no, 0.0) + flt(qty)
    return balance_map


def live_warehouses_by_batch(warehouse_balances):
    """{batch_no: {warehouse: qty}} for the positive entries of a
    get_batch_warehouse_balances map — indexed once so every stock group can
    look its batches up without scanning the whole map."""
    by_batch = {}
    for (batch_no, warehouse), qty in warehouse_balances.items():
        if flt(qty) > 0 and warehouse:
            by_batch.setdefault(batch_no, {})
            by_batch[batch_no][warehouse] = by_batch[batch_no].get(warehouse, 0.0) + flt(qty)
    return by_batch


def live_warehouses(batch_ids, by_batch):
    """Warehouses holding a positive live balance of any of these batches,
    largest first, as one display string ("Vadod - MC", or "Vadod - MC,
    Finished Goods - MC" for a lot split across two).

    MI1-I125 (Rohit 2026-09-05): the "Accepted Warehouse" column used to show
    Container.set_warehouse — where the container was inwarded — so after a
    Material Transfer (MAT-GD-2026-00011-1, MCL-29, Finished Goods - MC ->
    Vadod - MC) it still named the source warehouse. MI1-I103 settled that a
    stock movement never rewrites that field; the live location is the Serial
    and Batch Bundle balance, which is what this returns. Empty when none of
    the batches holds stock, so the caller can fall back to the inward value.
    """
    per_wh = {}
    for batch_no in batch_ids:
        for warehouse, qty in (by_batch.get(batch_no) or {}).items():
            per_wh[warehouse] = per_wh.get(warehouse, 0.0) + qty
    ordered = sorted(per_wh.items(), key=lambda kv: (-kv[1], kv[0]))
    return ", ".join(wh for wh, _qty in ordered)


def get_batch_rows(batch_ids):
    """Attribute rows of the given batches, in chunks, in the shape Step 3
    groups on. Only the stocked batches come through here (MI1-I119)."""
    rows = []
    for i in range(0, len(batch_ids), BALANCE_CHUNK):
        rows += frappe.db.sql(
            """
            SELECT name AS batch_id, item, custom_container_no AS container_no,
                   custom_lot_no AS lot_no, custom_cone AS cone, custom_pulp AS pulp,
                   custom_lusture AS lusture, custom_glue AS glue, custom_grade AS grade,
                   creation, batch_qty AS net_weight, custom_merge_no AS merge_no
            FROM `tabBatch`
            WHERE name IN %(names)s
            """,
            {"names": tuple(batch_ids[i : i + BALANCE_CHUNK])},
            as_dict=True,
        )
    return rows


def get_booked_quantities(batch_ids):
    """Per-booking details per batch from OPEN Sales Orders.

    Returns a dict: batch_id -> list of {booked_qty, sales_order, buyer,
    sales_person, lifting_terms, delivered_qty, delivered_weight, pending_qty,
    pending_weight}. Only bookings with a positive quantity are listed.

    MI1-I120 revision (Raj 2026-09-05): booking is released Sales-Order-wise.
    For a VFY order the effective booking is ordered − delivered (delivered =
    every submitted Delivery Note linked to the order, whichever batches it
    carried; drafts and cancelled notes excluded, returns netted), floored at
    zero and applied down the order's rows in order — so a booked batch that
    was never the one shipped is released once the order is served, and a
    closed or cancelled order books nothing. HTY keeps ERPNext's per-row
    `qty − delivered_qty`. The rule lives in
    mhr.utilis.sales_order_booking_state, shared with the Sales Order booking
    fetch and the lot popup, so the sheet, the fetch and the validation agree.
    """
    if not batch_ids:
        return {}

    from mhr.utilis import sales_order_booking_state

    booked_map = {}  # batch_id -> list of individual bookings
    CHUNK = 2000
    wanted = set(batch_ids)

    # Step 1: the booking state of every open order holding one of these batches
    state = {}
    for i in range(0, len(batch_ids), CHUNK):
        state.update(sales_order_booking_state(batch_names=batch_ids[i : i + CHUNK]))

    so_names = set()
    for sales_order, st in state.items():
        for r in st["rows"]:
            bid = r.get("batch")
            if bid not in wanted or flt(r.get("booked")) <= 0:
                continue
            so_names.add(sales_order)
            booked_map.setdefault(bid, []).append({
                "booked_qty": flt(r["booked"]),
                "sales_order": sales_order,
                "buyer": st.get("customer_name") or "",
                "sales_person": "",
                "lifting_terms": st.get("lifting_terms") or "",
                "delivered_qty": round(flt(st.get("delivered_qty")), 2),
                "delivered_weight": round(flt(st.get("delivered_weight")), 2),
                "pending_qty": round(flt(st.get("pending_qty")), 2),
                "pending_weight": round(flt(st.get("pending_weight")), 2),
            })

    # Step 2: Fetch sales persons per Sales Order from Sales Team child table
    sales_person_map = {}  # SO name -> comma-separated sales persons
    if so_names:
        ST = frappe.qb.DocType("Sales Team")
        so_list = list(so_names)
        for i in range(0, len(so_list), CHUNK):
            chunk = so_list[i : i + CHUNK]
            st_rows = (
                frappe.qb.from_(ST)
                .select(ST.parent, ST.sales_person)
                .where(ST.parent.isin(chunk))
                .where(ST.parenttype == "Sales Order")
            ).run(as_dict=True)
            for st in st_rows:
                if st.parent not in sales_person_map:
                    sales_person_map[st.parent] = []
                if st.sales_person and st.sales_person not in sales_person_map[st.parent]:
                    sales_person_map[st.parent].append(st.sales_person)

    # Step 3: Assign sales person to each booking
    for bid, bookings in booked_map.items():
        for bk in bookings:
            so_id = bk.get("sales_order")
            persons = sales_person_map.get(so_id, [])
            bk["sales_person"] = ", ".join(persons) if persons else ""

    return booked_map


def get_data(filters=None):
    if not filters:
        filters = {}

    fdt = filters.get("fdt")
    tdt = filters.get("tdt")
    container = filters.get("container")
    lot_no = filters.get("lot_no")
    cone = filters.get("cone")
    company = filters.get("company")
    transaction_type = filters.get("transaction_type")


    # Step 1: Query filtered batches — names first.
    #
    # MI1-I119 (Raj 2026-09-02): a full-range run loaded all 378K batches with
    # twelve columns, then aggregated their bundle balances 2000 at a time
    # through the bundle table's primary key — 85 s locally, 73 of them in
    # that join, and frappe flipped the report into prepared-report mode
    # (which is why prod showed "Rebuild" / "Generate New Report" and the HTY
    # run never came back). Now: names only, balances through the covering
    # `idx_sbb_name_status` index (mhr.patches.v1_0.add_serial_batch_bundle_
    # status_index) in chunks of 5000 that return only non-zero (batch,
    # warehouse) pairs, then the full rows for the ~80K batches that actually
    # hold stock — the only ones the sheet can render. Same figures, ~6x
    # faster, and the report runs inline again.
    Batch = frappe.qb.DocType("Batch")
    conditions = []

    if fdt:
        conditions.append(Batch.creation >= fdt)
    if tdt:
        # MI1-I91: Batch.creation is a DATETIME, so `creation <= '2026-08-11'`
        # resolves to `<= 2026-08-11 00:00:00` and silently drops every batch
        # created *today*. Compare against the start of the next day so To Date
        # is inclusive of the whole day, which is what the filter label implies.
        conditions.append(Batch.creation < add_days(getdate(tdt), 1))

    if container:
        conditions.append(Batch.custom_container_no == container)
    if lot_no:
        conditions.append(Batch.custom_lot_no == lot_no)
    if cone:
        conditions.append(Batch.custom_cone == cone)

    # Container-scoped filters (company + transaction type).
    #
    # MI1-I99 (2026-08-17): transaction_type used to be applied AFTER the rows
    # were built, by filtering the finished list in execute(). Two things went
    # wrong with that:
    #   1. Every total row carries a blank "Container Number" (see Step 8), and
    #      the post-filter kept only rows whose container was in the allowed
    #      set — so the Grand Total, and every lot / container subtotal,
    #      silently vanished the moment a Transaction Type was picked.
    #   2. Even when visible, the totals had been summed over the UNfiltered
    #      rows, so they did not describe what the user was looking at.
    # Restricting the batch query up front fixes both: every downstream tier
    # (Step 5 lot totals, Step 6 container totals, Step 7b grand total) is now
    # computed from exactly the rows the report renders.
    #
    # Both filters narrow the same column, so they are intersected into a
    # single IN clause rather than stacked — idx_custom_container_no covers it.
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

    # How many batches do the filters leave? A narrow set (one container, a
    # lot, a fortnight of inward) is named and aggregated directly — that is
    # the sub-second path a scoped run always had. A wide set (a full year,
    # a whole Company or mode) reads the whole-site map instead of naming
    # hundreds of thousands of batches first.
    from frappe.query_builder.functions import Count

    filtered_count = _batch_query(Count(Batch.name).as_("n")).run(pluck="n")[0]
    query = _batch_query(Batch.name.as_("batch_id"))
    if container or lot_no or cone or filtered_count < WHOLE_SITE_FROM:
        # A narrow set: name it, then aggregate only its batches.
        batch_ids = query.run(pluck="batch_id")
        if not batch_ids:
            return []
        # Step 2: Live stock per batch and per (batch, warehouse)
        warehouse_balances = get_batch_warehouse_balances(batch_ids)
        balance_map = get_batch_balances(batch_ids, warehouse_balances)
        # Only batches with stock can appear on the sheet (Step 4 drops the
        # rest), so only those need their attributes loaded.
        stocked_ids = [b for b in batch_ids if flt(balance_map.get(b, 0)) > 0]
        batches = get_batch_rows(stocked_ids)
    else:
        # Dates, Company and Transaction Type alone: the whole site's balance
        # map (cached — see get_all_warehouse_balances) names the ~80K stocked
        # batches; their rows are loaded once and the filters applied to those
        # in Python, instead of naming all 500K batches first.
        warehouse_balances = get_all_warehouse_balances()
        balance_map = get_batch_balances(list(warehouse_balances), warehouse_balances)
        stocked_all = [b for b, q in balance_map.items() if flt(q) > 0]
        batches = get_batch_rows(stocked_all)
        lo = datetime.combine(getdate(fdt), datetime.min.time()) if fdt else None
        hi = datetime.combine(add_days(getdate(tdt), 1), datetime.min.time()) if tdt else None
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
    # Primary-key order, as the single Batch query used to return them: the
    # first batch of a group decides its Merge No, and rows with equal sort
    # keys keep their insertion order.
    batches.sort(key=lambda b: b.batch_id)

    # Step 2b: Get per-booking details per batch
    booked_map = get_booked_quantities(stocked_ids)

    # Step 2c: Get cross_section, notes, warehouse from Container doctype
    container_keys = set()
    for b in batches:
        container_keys.add((b.container_no or "", b.lot_no or ""))
    container_info = {}  # (container_no, lot_no) -> {cross_section, notes, location, accepted_warehouse}
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
                if ck in container_keys:
                    container_info[ck] = {
                        "cross_section": cr.cross_section or "",
                        "notes": cr.notes or "",
                        "location": cr.warehouse or "",
                        "production_date": str(cr.production_date) if cr.production_date else "",
                        "accepted_warehouse": cr.set_warehouse or "",
                    }

    # Step 3: Aggregate by group key in Python
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
                "balance": 0.0,
                "balance_box": 0,
                "booked_qty": 0.0,
                "bookings": [],  # list of individual booking dicts
                "merge_no": b.merge_no or "",
                "cross_section": ci.get("cross_section", ""),
                "production_date": ci.get("production_date", ""),
                "notes": ci.get("notes", ""),
                "location": ci.get("location", ""),
                # MI1-I125: resolved from live stock once the group is
                # complete (see below); the inward warehouse is the fallback.
                "accepted_warehouse": ci.get("accepted_warehouse", ""),
                "stocked_batches": [],
            }

        if flt(balance_map.get(b.batch_id, 0)) > 0:
            groups[key]["balance"] += flt(b.net_weight)
            groups[key]["balance_box"] += 1
            groups[key]["stocked_batches"].append(b.batch_id)

        # Collect individual bookings for this batch, consolidate by sales order
        bk_list = booked_map.get(b.batch_id)
        if bk_list:
            for bk in bk_list:
                groups[key]["booked_qty"] += bk["booked_qty"]
                # Merge into existing booking if same sales order
                so_id = bk.get("sales_order", "")
                existing = None
                for eb in groups[key]["bookings"]:
                    if eb.get("sales_order") == so_id and so_id:
                        existing = eb
                        break
                if existing:
                    existing["booked_qty"] += bk["booked_qty"]
                else:
                    groups[key]["bookings"].append(dict(bk))

    # Step 4: Filter - balance_box > 0, balance > 0, and cone > 0 for VFY only.
    #
    # MI1-I91 (2026-08-11): HTY carries non-coned material — Chips and Waste
    # are issued in Bags, so `custom_cone` is legitimately 0. The blanket
    # `cone > 0` guard was throwing those rows away before they reached the
    # report, which hid 4,046 of the 4,943 HTY batches: "Chips" never appeared
    # in the Product column AND its stock never appeared, because the row did
    # not exist at all.
    #
    # Decided per row from the row's own Container rather than from the
    # Transaction Type filter, so the rule still holds on the unfiltered
    # ("All") view. VFY rows keep the original guard byte-for-byte — a yarn
    # batch with no cones is still junk and stays hidden.
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

        if g["balance_box"] > 0 and flt(g["balance"]) > 0:
            g["sort_order"] = 0
            g["report_date"] = g["batch_date"].strftime("%d/%m/%Y")
            # MI1-I125: where the stock IS, not where the container came in.
            g["accepted_warehouse"] = (
                live_warehouses(g["stocked_batches"], stocked_by_batch) or g["accepted_warehouse"]
            )
            g["available_qty"] = round(flt(g["balance"]) - flt(g["booked_qty"]), 2)
            # Identifies which rendered rows came out of this one stock group.
            # Step 8 emits one full row per Sales Order booking, repeating
            # Balance / Balance Box / Cone on each, so the browser needs a way
            # to count those group-level figures once when it re-totals the
            # rows left over by a column filter (see the report's .js).
            g["group_key"] = len(main_rows)
            main_rows.append(g)

    if not main_rows:
        return []

    # Step 5: Compute lot totals (sort_order=1)
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
                "pulp": "",
                "lusture": "",
                "glue": "Total:",
                "grade": "",
                "balance": round(sum(r["balance"] for r in rows), 2),
                "lot_no": lot,
                "balance_box": sum(r["balance_box"] for r in rows),
                "cone": "",
                "sort_order": 1,
                "booked_qty": round(sum(r["booked_qty"] for r in rows), 2),
                "available_qty": round(sum(r["available_qty"] for r in rows), 2),
                "bookings": [],
                "merge_no": "",
                "cross_section": "",
                "production_date": "",
                "notes": "",
                "location": "",
                "accepted_warehouse": "",
            }
        )

    # Step 6: Compute container totals (sort_order=2, only when multiple lots)
    container_groups = defaultdict(list)
    for row in main_rows:
        ck = row["container_no"]
        container_groups[ck].append(row)

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
                    "pulp": "",
                    "lusture": "",
                    "glue": "Grand Total:",
                    "grade": "",
                    "balance": round(sum(r["balance"] for r in rows), 2),
                    "lot_no": "",
                    "balance_box": sum(r["balance_box"] for r in rows),
                    "cone": "",
                    "sort_order": 2,
                    "booked_qty": round(sum(r["booked_qty"] for r in rows), 2),
                    "available_qty": round(sum(r["available_qty"] for r in rows), 2),
                    "bookings": [],
                    "merge_no": "",
                    "cross_section": "",
                    "production_date": "",
                    "notes": "",
                    "location": "",
                    "accepted_warehouse": "",
                }
            )

    # Step 7: Combine and sort
    #
    # MI1-I119 (prod 2026-09-08): `cone` is an Int on the Batch, so detail rows
    # carry 12 while a Chips row (cone 0, HTY — MI1-I91) and every total row
    # carry "" — and Python refuses to order 12 against "". Every HTY run on
    # prod died here with "'<' not supported between instances of 'int' and
    # 'str'" (Prepared Report 3jhahn82je), which is the "HTY is not
    # generating" half of the ticket. Sort on the integer value; blanks are 0.
    all_rows = main_rows + lot_totals + container_totals
    all_rows.sort(
        key=lambda r: (
            -r["batch_date"].toordinal(),
            r["container_no"],
            r["lot_no"] if r["lot_no"] else "\xff",
            r["sort_order"],
            cint(r["cone"]),
        )
    )

    # Step 7b: Compute report-level grand total (only from detail rows, sort_order=0)
    report_total = {
        "balance": round(sum(r["balance"] for r in main_rows), 2),
        "balance_box": sum(r["balance_box"] for r in main_rows),
        "booked_qty": round(sum(r["booked_qty"] for r in main_rows), 2),
        "available_qty": round(sum(r["available_qty"] for r in main_rows), 2),
        "cone": sum(int(r["cone"]) if r["cone"] else 0 for r in main_rows),
    }

    # Step 8: Format output — one FULL row per Sales Order allocation.
    #
    # MI1-I94 (Raj 2026-08-13): every Sales Order booked against a
    # container/lot now renders as its own COMPLETE row (was: first SO
    # got a full row; subsequent SOs got sparse sub-rows with blank
    # Balance / Item / etc.). Per Raj:
    #   * `Booked Qty` (Buyer Qty field) — this SO's booking (unchanged)
    #   * `Total Booked` (Booked Qty field) — this SO's booking too
    #     (was: sum of every SO on the group — combined across SOs)
    #   * `Available Qty` — Balance minus THIS SO's booking (was:
    #     progressive across all bookings — treated the whole group
    #     as one allocation stack)
    #   * `Buyer` / `Sales Person` / `Lifting Terms` from this SO
    #   * `Balance`, `Item`, `Container`, etc. repeat on every SO row
    #     so each row is self-contained.
    # Aging (also new in MI1-I94): today − batch_date in days, on
    # detail rows only. Totals leave it blank.
    today_date = getdate(today())

    def _aging_for(row):
        """Days on hand, or None.

        None (not "") is what renders as an empty cell: the column is an Int,
        and frappe's Int formatter only short-circuits on null — cint("") is 0,
        so an empty string would print "0".

        A same-day batch is 0 days old and prints blank too (2026-08-18): the
        column is scanned for stock that has been sitting, and a column of
        zeros on the freshest rows just adds noise.
        """
        bd = row.get("batch_date")
        if not bd:
            return None
        return date_diff(today_date, bd) or None

    result = []
    for row in all_rows:
        so = row["sort_order"]
        bookings = row.get("bookings", [])

        # Base output dict for this stock row
        base = {
            "Date": "" if so >= 1 else row["report_date"],
            "Container Number": "" if so >= 1 else row["container_no"],
            "Item": row["item"],
            "Pulp": strip_prefix(row["pulp"]) if so == 0 else row["pulp"],
            "Lusture": strip_prefix(row["lusture"]) if so == 0 else row["lusture"],
            "Glue": strip_prefix(row["glue"]) if so == 0 else row["glue"],
            "Grade": strip_prefix(row["grade"]) if so == 0 else row["grade"],
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
            # Not a column — a marker the report's .js reads off the row dict
            # to re-total the Qty columns after a datatable column filter.
            "_group_key": row.get("group_key") if so == 0 else None,
        }

        # MI1-I120 revision: the order's delivery picture rides on its row.
        for col in ("Delivered Qty", "Delivered Weight", "Pending Qty", "Pending Weight"):
            base[col] = ""

        if so != 0 or not bookings:
            # Total/grand-total rows, or detail rows with no bookings.
            base["Sales Order"] = ""
            base["Buyers"] = ""
            base["Sales Person"] = ""
            base["Buyer Qty"] = ""
            base["Lifting Terms"] = ""
            result.append(base)
        else:
            # Per-SO expansion: one FULL row per booking. Each row is
            # self-contained — Balance / Item / Container / Aging /
            # etc. repeat, and Booked/Total Booked/Available are
            # independent (not progressive) so each SO row reads as
            # its own allocation view.
            balance = flt(row["balance"])
            for bk in bookings:
                bk_qty = flt(bk["booked_qty"])
                so_row = dict(base)
                so_row["Booked Qty"] = round(bk_qty, 2)        # "Total Booked" column
                so_row["Buyer Qty"] = round(bk_qty, 2) if bk_qty else ""  # "Booked Qty" column
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

    # Append report-level grand total row
    result.append({
        "Date": "",
        "Container Number": "",
        "Item": "<b>Total</b>",
        "Pulp": "",
        "Lusture": "",
        "Glue": "",
        "Grade": "",
        "Balance": report_total["balance"],
        "Lot Number": "",
        "Balance Box": report_total["balance_box"],
        "Cone": report_total["cone"],
        "Booked Qty": report_total["booked_qty"],
        "Available Qty": report_total["available_qty"],
        "Sales Order": "",
        "Buyers": "",
        "Sales Person": "",
        # MI1-I99: the "Booked Qty" COLUMN is fieldname `Buyer Qty`, and
        # "Total Booked" is fieldname `Booked Qty` — the two are crossed over
        # (see get_columns). This was left blank, so one of the five requested
        # Qty columns had no grand total. Step 8 writes the same per-SO figure
        # into both fields, so both total to report_total["booked_qty"].
        "Buyer Qty": report_total["booked_qty"],
        "Lifting Terms": "",
        "Delivered Qty": "",
        "Delivered Weight": "",
        "Pending Qty": "",
        "Pending Weight": "",
        "Merge No": "",
        "Cross Section": "",
        "Production Date": "",
        "Notes": "",
        "Location": "",
        "Accepted Warehouse": "",
        "Aging": None,
        "sort_order": 3,
        "_group_key": None,
    })

    return result
