import frappe
from frappe.utils import cint


@frappe.whitelist()
def get_hty_batches_by_item(item, limit_start=0, limit_page_length=50):
    """MI1-I71 (Raj 2026-07-17): HTY popup's item-scoped batch source.
    Replaces a JS `frappe.client.get_list('Batch', ...)` call so the
    'Batch Qty' column reflects the CURRENT available balance (from
    Serial and Batch Bundle), not the stale Batch master value that
    drifts after partial consumption.

    Preserves the client's page_size-based pagination contract: caller
    keeps requesting pages until an empty page comes back. Zero-balance
    rows are kept in the response (batch_qty=0) so pagination math stays
    correct across depleted-page boundaries; the popup's Select handler
    is the layer that skips 0-qty picks.
    """
    if not item:
        return []

    batches = frappe.get_all(
        "Batch",
        filters={"item": item},
        fields=[
            "name", "custom_glue", "custom_pulp", "custom_lusture",
            "custom_grade", "custom_lot_no", "custom_fsc", "custom_cone",
            "item", "item_name", "manufacturing_date", "cross_section",
            "batch_qty", "stock_uom", "expiry_date", "supplier",
            "custom_supplier_batch_no", "custom_container_no",
            "custom_merge_no", "custom_warehouse",
        ],
        limit_start=cint(limit_start),
        limit_page_length=cint(limit_page_length),
        order_by="name asc",
    )
    if not batches:
        return []

    _clamp_batch_qty_to_available(batches, False)
    return batches


# MI1-I103: the LIMIT used to be applied before the availability check, so rows
# dropped for being consumed ate into the requested count. Scan a multiple,
# filter, then trim — "fetch 5" returns the 5 lowest AVAILABLE batches.
SCAN_MULTIPLIER = 5

# Ceiling on that scan. Both callers filter by container and the largest here
# holds 800 batches, so this bounds a caller that passes no limit at all.
MAX_SCAN = 2000


@frappe.whitelist()
def fetch_batches(
    limit,
    lot_no=None,
    container_no=None,
    glue=None,
    pulp=None,
    fsc=None,
    lusture=None,
    grade=None,
    cone=None,
    denier=None,
    is_return = False,
    warehouse=None,
):
    """Batches matching the header spec, ascending by Supplier Batch No.

    MI1-I103: `warehouse` scopes availability to one warehouse. Without it an
    already-sent batch still counts as available, because its balance simply
    moved to the subcontractor's warehouse. Stock Entry passes its source
    warehouse; Delivery Note passes none and keeps the any-warehouse behaviour.
    """

    filters = {}

    # Add filters based on available parameters
    if lot_no:
        filters["custom_lot_no"] = lot_no
    if container_no:
        filters["custom_container_no"] = container_no

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
        from mhr.utilis import grade_filter_value

        filters["custom_grade"] = grade_filter_value(grade)
    if cone and is_return is False:
        filters["custom_cone"] = cone
    if denier and is_return is False:
        filters["item_name"] = denier

    # MI1-I85 (Raj 2026-07-18): don't return zero-cone batches from the
    # Fetch Batches flow — they hit the DN as qty=0 child rows that
    # can't be submitted anyway. Skip on return receipts (returns are
    # allowed to reference depleted-cone batches).
    if is_return is False:
        filters["custom_cone"] = filters.get("custom_cone") or [">", 0]

    if not filters:
        return []

    # MI1-I103: ERPNext never ticks Batch.disabled itself, so it only ever means
    # someone deliberately took that batch out of circulation. Added after the
    # empty check so a caller passing nothing still gets nothing.
    filters["disabled"] = 0

    requested = cint(limit) or 0
    scan_limit = min(requested * SCAN_MULTIPLIER, MAX_SCAN) if requested else MAX_SCAN

    batches = frappe.get_all(
        "Batch",
        filters=filters,
        fields=["name", "item", "item_name", "batch_qty", "stock_uom", "custom_supplier_batch_no", "custom_cone", "custom_lusture", "custom_grade", "custom_glue", "custom_pulp", "custom_fsc", "custom_lot_no", "custom_container_no", "custom_notes"],
        # MI1-I103: there was no order_by at all — MAT-GD-2026-00008 landed
        # 6876, 6870, 6872, 6879, 6874. This decides which rows survive the
        # scan limit; the numeric sort below decides how they are presented.
        order_by="custom_supplier_batch_no asc, name asc",
        limit=scan_limit,
    )

    # MI1-I71 (Raj 2026-07-15): the client uses `batch_qty` to
    # populate the new DN row's qty. Historically that was the
    # ORIGINAL batch qty at creation, which overdrafts partially-
    # consumed batches (submit fails with negative-stock). Clamp
    # each batch's `batch_qty` to the current AVAILABLE balance
    # (from Serial and Batch Bundle), so the row lands with a qty
    # that will actually submit.
    _clamp_batch_qty_to_available(batches, is_return, warehouse)

    # MI1-I85 (Raj 2026-07-18): drop batches whose clamped qty is
    # 0 — they'd become zero-quantity DN rows which can never
    # submit. On return receipts (is_return=True), the clamp isn't
    # applied, so batch_qty stays as the master value; keep those.
    if is_return is False:
        batches = [b for b in batches if float(b.get("batch_qty") or 0) > 0]

    batches.sort(key=supplier_batch_sort_key)

    return batches[:requested] if requested else batches


def supplier_batch_sort_key(batch):
    """MI1-I103: ascending by Supplier Batch No.

    The column is Data, so SQL sorts it as text and '10' lands before '9'.
    Non-numeric values (a blank included) fall back to the string and sort
    last, keeping a total order instead of raising.
    """
    raw = str(batch.get("custom_supplier_batch_no") or "").strip()
    try:
        return (0, float(raw), "")
    except (TypeError, ValueError):
        return (1, 0.0, raw)


def _clamp_batch_qty_to_available(batches, is_return, warehouse=None):
    """MI1-I71 helper: overwrite each row's `batch_qty` with the
    current available balance from Serial and Batch Bundle. Return
    receipts (is_return=True) are left alone since they add back to
    stock, not consume it.

    Also fills a new `warehouse` key with the warehouse holding the
    largest positive balance for the batch, so the client can set
    `s_warehouse` on the item row (see MI1-I78 P7).

    MI1-I103: `warehouse` counts the balance in that warehouse only. An
    already-sent batch still has a positive balance, just not where the next
    Send entry draws from, so unscoped it survives the filter and is offered
    again.
    """
    if is_return or not batches:
        return
    batch_names = [b.get("name") for b in batches if b.get("name")]
    if not batch_names:
        return

    placeholders = ", ".join(["%s"] * len(batch_names))
    params = list(batch_names)
    warehouse_clause = ""
    if warehouse:
        warehouse_clause = " AND sbb.warehouse = %s"
        params.append(warehouse)

    rows = frappe.db.sql(
        f"""
        SELECT sbe.batch_no, sbb.warehouse, SUM(sbe.qty) AS balance
        FROM `tabSerial and Batch Bundle` sbb
        INNER JOIN `tabSerial and Batch Entry` sbe ON sbe.parent = sbb.name
        WHERE sbe.batch_no IN ({placeholders})
          AND sbb.docstatus = 1
          AND sbb.is_cancelled = 0
          AND sbb.type_of_transaction IN ('Inward', 'Outward')
          {warehouse_clause}
        GROUP BY sbe.batch_no, sbb.warehouse
        HAVING balance > 0
        """,
        tuple(params),
        as_dict=True,
    )
    # Per-batch: pick the warehouse with the largest positive balance.
    per_batch = {}
    for r in rows:
        cur = per_batch.get(r["batch_no"])
        if cur is None or r["balance"] > cur["balance"]:
            per_batch[r["batch_no"]] = r

    for b in batches:
        entry = per_batch.get(b.get("name"))
        if entry is None:
            # No positive balance anywhere — clamp to 0 so the client
            # can decide to skip / warn instead of over-drafting.
            b["batch_qty"] = 0
            b["warehouse"] = None
            continue
        original = float(b.get("batch_qty") or 0)
        available = float(entry["balance"])
        b["batch_qty"] = min(original, available) if original > 0 else available
        b["warehouse"] = entry["warehouse"]


@frappe.whitelist()
def get_container_notes(container_no, transaction_type=None):
    """MI1-I101: Container Inward `notes` for the Delivery Note form.

    Backs the on-change handler so `custom_notes` refreshes the moment the
    user picks or switches a Container No, instead of only being backfilled
    at save time by mhr.utilis.fetch_notes_from_container.
    """
    frappe.has_permission("Delivery Note", "write", throw=True)
    from mhr.utilis import resolve_container_notes

    return resolve_container_notes(container_no, transaction_type) or ""
