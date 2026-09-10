# Copyright (c) 2026, reformiqo and contributors
# For license information, please see license.txt
#
# MI1-I135 (Raj 2026-09-10): "an exact replica of Stock Sheet (Balance
# Report) ... as a new report without modifying the existing report", with
# the changes in MHR_Stock_Sheet_Book2_Reviewed_v3.0.xlsx (the "Book2" FRD,
# reviewed and corrected to v3.0 by Reformiqo's own analyst before this was
# built). Four new columns — In Qty, Out Qty, GR Received, Job work Send Qty
# — are inserted immediately after Glue and before Balance Qty, and Balance
# Qty itself changes from "live Serial and Batch Bundle balance" to a
# movement-ledger formula: In Qty - Out Qty + GR Received - Job work Send Qty
# (the FRD's sheet 1 marks this formula "accepted as given"). Nothing else
# moves — Booked Qty, Available Qty (now Balance Qty minus Booked, using the
# new Balance), Delivered/Pending, Accepted Warehouse, HTY/VFY column swaps,
# per-Sales-Order row expansion, lot/container/grand totals, all behave
# exactly as the original.
#
# The generic balance/warehouse/booking primitives (whole-site balance map,
# Redis caching, FORCE INDEX hint, live warehouse resolution, Sales Order
# booking state) are IMPORTED from the original report rather than
# duplicated — they are unrelated to what Book2 asks to change, and any
# future fix to them (index maintenance, a new warehouse rule) should not
# need to be applied twice. Only the parts Book2 actually touches —
# get_columns, and the group-assembly / totals inside get_data — are
# rewritten here.
#
# Known limitation, carried over verbatim from the FRD's own "Issues to
# Settle" sheet (Issue 7): Balance Qty here covers only four movement types
# (Container Inward, Job Work Received, Delivery, Sales Return, Send to
# Subcontractor). It does not cover Purchase Receipt, Material Issue, Stock
# Reconciliation, transfers between the company's own warehouses, or scrap.
# If any of those occur against VFY/HTY stock, THIS report's Balance Qty can
# disagree with the original Stock Sheet (Balance Report) and with Stock
# Ledger for the same row — that is why the original is left untouched
# rather than modified in place, and why "Accepted Warehouse" (still sourced
# from the live Serial and Batch Bundle balance, unchanged) can show a
# location even on a row whose movement-ledger Balance Qty is very small,
# zero, or disagrees with what is actually in that warehouse.

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
get_booked_quantities = _original.get_booked_quantities
strip_prefix = _original.strip_prefix
WHOLE_SITE_FROM = _original.WHOLE_SITE_FROM
_original_get_columns = _original.get_columns

REPORT_NAME = "STOCK SHEET (BALANCE REPORT) v2"


def execute(filters=None):
    filters = filters or {}
    started_inline = _runs_inline()
    from mhr.utilis import enforce_role_scoped_transaction_type
    filters = enforce_role_scoped_transaction_type(filters)
    columns = get_columns(filters)
    data = get_data(filters)
    keep_inline(started_inline)
    return columns, data


def _runs_inline():
    try:
        return not frappe.db.get_value("Report", REPORT_NAME, "prepared_report")
    except Exception:
        return False


def keep_inline(started_inline):
    """Same safety valve as the original report's own keep_inline (MI1-I119)
    — a separate Report record, so a separate flag frappe's 15s watcher can
    flip independently."""
    if not started_inline:
        return
    try:
        if frappe.db.get_value("Report", REPORT_NAME, "prepared_report"):
            frappe.db.set_value("Report", REPORT_NAME, "prepared_report", 0, update_modified=False)
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


def get_movement_totals(container=None, lot_no=None, cone=None):
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
    """Byte-for-byte the original's get_data (see that file for the full
    Step-by-step commentary — Batch query, whole-site vs narrow balance
    path, Sales Order booking, HTY cone exemption, per-SO row expansion, lot
    / container / grand totals), with exactly two differences:

      1. Each stock group's Balance / Balance Box come from
         get_movement_totals() (In - Out + GR - JW Send) instead of the
         live Serial and Batch Bundle balance, and the four movement figures
         ride along on every row (Step 8) and every total (Step 5/6/7b) the
         same way Balance / Balance Box already did.
      2. Live SBB data is still used for exactly what it always was —
         deciding which batches are "stocked" for Accepted Warehouse — never
         for Balance Qty itself any more.
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

    # Narrowed by whichever of container / lot_no / cone the user gave —
    # exactly the fields the movement row key itself uses, so this is always
    # safe, unlike narrowing by date (a delivery or send can land well
    # outside the batch's own creation-date window).
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
        batch_ids = query.run(pluck="batch_id")
        if not batch_ids:
            return []
        # MI1-I135: unlike the original, "is this batch shown" is no longer
        # decided by the live Serial and Batch Bundle balance — it is
        # decided by the movement ledger (Step 4). Gating attribute-loading
        # on a live balance here (the original's own optimization, valid
        # only because ITS visibility rule IS the live balance) would drop a
        # batch whose movement-ledger balance is positive but whose live SBB
        # balance happens to be zero — the exact batches Job Work Received
        # test data exposed, and a real possibility whenever a stock event
        # outside the five tracked movement types touches a lot (Issue 7).
        # warehouse_balances / balance_map are still built, for Accepted
        # Warehouse only (Step 3).
        warehouse_balances = get_batch_warehouse_balances(batch_ids)
        balance_map = get_batch_balances(batch_ids, warehouse_balances)
        batches = get_batch_rows(batch_ids)
        stocked_ids = batch_ids
    else:
        # Whole-site (no Container / Lot / Cone filter): the cached map is
        # scoped to batches with a live SBB balance, by design (see
        # get_all_warehouse_balances) — the same known-limitation trade-off
        # as above applies here too, and there is no whole-site index of
        # "batches with any of the five tracked movements" to name the full
        # candidate set without a 500K-row Batch scan. A Container / Lot
        # filter always gets the correct, ungated answer above; an
        # unfiltered run of this report can under-report a lot whose stock
        # left the live-balance set through a movement this report does not
        # track.
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

    booked_map = get_booked_quantities(stocked_ids)

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
                if ck in container_keys:
                    container_info[ck] = {
                        "cross_section": cr.cross_section or "",
                        "notes": cr.notes or "",
                        "location": cr.warehouse or "",
                        "production_date": str(cr.production_date) if cr.production_date else "",
                        "accepted_warehouse": cr.set_warehouse or "",
                    }

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
                {"in_qty": 0.0, "out_qty": 0.0, "gr_qty": 0.0, "jw_qty": 0.0,
                 "in_box": 0, "out_box": 0, "gr_box": 0, "jw_box": 0},
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
                # MI1-I135: movement-ledger balance, not the live SBB balance.
                # Rounded here, like balance on the next line — SUM() over
                # many decimal qty rows leaves float noise (3044.0000000000027)
                # that round() only removes once, at the source, not every
                # place downstream that reads in_qty/out_qty/gr_qty/jw_qty.
                "in_qty": round(mv["in_qty"], 2), "out_qty": round(mv["out_qty"], 2),
                "gr_qty": round(mv["gr_qty"], 2), "jw_qty": round(mv["jw_qty"], 2),
                "balance": round(mv["in_qty"] - mv["out_qty"] + mv["gr_qty"] - mv["jw_qty"], 2),
                "balance_box": mv["in_box"] - mv["out_box"] + mv["gr_box"] - mv["jw_box"],
                "booked_qty": 0.0,
                "bookings": [],
                "merge_no": b.merge_no or "",
                "cross_section": ci.get("cross_section", ""),
                "production_date": ci.get("production_date", ""),
                "notes": ci.get("notes", ""),
                "location": ci.get("location", ""),
                "accepted_warehouse": ci.get("accepted_warehouse", ""),
                "stocked_batches": [],
            }

        # Still live-SBB-based, same as the original: which batches are
        # actually stocked, for the Accepted Warehouse column only.
        if flt(balance_map.get(b.batch_id, 0)) > 0:
            groups[key]["stocked_batches"].append(b.batch_id)

        bk_list = booked_map.get(b.batch_id)
        if bk_list:
            for bk in bk_list:
                groups[key]["booked_qty"] += bk["booked_qty"]
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

        # MI1-I135: a group only makes the sheet while it is net "in" on the
        # movement ledger (Balance > 0), matching the original's stocked-only
        # rule but sourced from the ledger instead of the live SBB balance.
        if g["balance_box"] > 0 and flt(g["balance"]) > 0:
            g["sort_order"] = 0
            g["report_date"] = g["batch_date"].strftime("%d/%m/%Y")
            g["accepted_warehouse"] = (
                live_warehouses(g["stocked_batches"], stocked_by_batch) or g["accepted_warehouse"]
            )
            g["available_qty"] = round(flt(g["balance"]) - flt(g["booked_qty"]), 2)
            g["group_key"] = len(main_rows)
            main_rows.append(g)

    if not main_rows:
        return []

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
