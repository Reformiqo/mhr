import frappe

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
):

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
        filters["custom_grade"] = grade
    if cone and is_return is False:
        filters["custom_cone"] = cone
    if denier and is_return is False:
        filters["item_name"] = denier

    if filters:
        batches = frappe.get_all("Batch", filters=filters, fields=["name", "item", "item_name", "batch_qty", "stock_uom", "custom_supplier_batch_no", "custom_cone", "custom_lusture", "custom_grade", "custom_glue", "custom_pulp", "custom_lusture", "custom_grade", "custom_glue", "custom_pulp", "custom_fsc", "custom_lot_no", "custom_container_no", "custom_notes"], limit=limit)

        # MI1-I71 (Raj 2026-07-15): the client uses `batch_qty` to
        # populate the new DN row's qty. Historically that was the
        # ORIGINAL batch qty at creation, which overdrafts partially-
        # consumed batches (submit fails with negative-stock). Clamp
        # each batch's `batch_qty` to the current AVAILABLE balance
        # (from Serial and Batch Bundle), so the row lands with a qty
        # that will actually submit.
        _clamp_batch_qty_to_available(batches, is_return)
        return batches
    else:
        return []


def _clamp_batch_qty_to_available(batches, is_return):
    """MI1-I71 helper: overwrite each row's `batch_qty` with the
    current available balance from Serial and Batch Bundle. Return
    receipts (is_return=True) are left alone since they add back to
    stock, not consume it.

    Also fills a new `warehouse` key with the warehouse holding the
    largest positive balance for the batch, so the client can set
    `s_warehouse` on the item row (see MI1-I78 P7).
    """
    if is_return or not batches:
        return
    batch_names = [b.get("name") for b in batches if b.get("name")]
    if not batch_names:
        return

    placeholders = ", ".join(["%s"] * len(batch_names))
    rows = frappe.db.sql(
        f"""
        SELECT sbe.batch_no, sbb.warehouse, SUM(sbe.qty) AS balance
        FROM `tabSerial and Batch Bundle` sbb
        INNER JOIN `tabSerial and Batch Entry` sbe ON sbe.parent = sbb.name
        WHERE sbe.batch_no IN ({placeholders})
          AND sbb.docstatus = 1
          AND sbb.is_cancelled = 0
          AND sbb.type_of_transaction IN ('Inward', 'Outward')
        GROUP BY sbe.batch_no, sbb.warehouse
        HAVING balance > 0
        """,
        tuple(batch_names),
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
