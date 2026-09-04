import frappe
from frappe.utils import cint, flt


@frappe.whitelist()
def get_so_batches(
    item_code, container_no=None, lot_no=None, cone=0, qty=0, boxes=0,
    pallets=0, transaction_type=None,
):
    """Fetch batches for Sales Order based on item, container, lot and auto-split by cone, qty or boxes.

    MI1-I91 (Raj 2026-09-03): HTY reuses this allocation unchanged. `pallets`
    is the HTY name for `boxes` (1 batch = 1 pallet, exactly as 1 batch = 1
    box) and `transaction_type` scopes the Batch query to that mode's batches
    via Batch.custom_transaction_type. Both default off, so every VFY call
    site keeps its previous behaviour byte-for-byte.
    """
    qty = flt(qty)
    cone = int(cone or 0)
    boxes = int(boxes or 0) or int(pallets or 0)
    filters = {"item": item_code, "batch_qty": (">", 0)}
    if transaction_type:
        filters["custom_transaction_type"] = transaction_type
    if container_no:
        # container_no may be a Container doc name; resolve to the container_no field value
        actual_container_no = frappe.db.get_value("Container", container_no, "container_no") or container_no
        filters["custom_container_no"] = actual_container_no
    if lot_no:
        filters["custom_lot_no"] = lot_no

    batches = frappe.get_all(
        "Batch",
        filters=filters,
        fields=[
            "name", "item", "item_name", "batch_qty", "stock_uom",
            "custom_supplier_batch_no", "custom_cone", "custom_container_no",
            "custom_lot_no", "custom_lusture", "custom_grade", "custom_glue",
            "custom_pulp", "custom_fsc",
        ],
        order_by="custom_supplier_batch_no asc",
    )

    if boxes and cone:
        # Filter batches that have exactly the requested cone count, then pick N boxes
        result = []
        remaining_boxes = boxes
        for b in batches:
            if remaining_boxes <= 0:
                break
            batch_cones = int(b.custom_cone or 0)
            if batch_cones != cone:
                continue
            available = _get_available_qty(b.name, b.batch_qty)
            if available <= 0:
                continue
            available_cones = _get_available_cones(b.name, batch_cones)
            if available_cones <= 0:
                continue
            b["available_qty"] = available
            b["allotted_qty"] = available
            b["allotted_cones"] = available_cones
            result.append(b)
            remaining_boxes -= 1
        return result

    if boxes:
        # Allocate by number of boxes (1 batch = 1 box), take full available qty from each
        result = []
        remaining_boxes = boxes
        for b in batches:
            if remaining_boxes <= 0:
                break
            available = _get_available_qty(b.name, b.batch_qty)
            if available <= 0:
                continue
            b["available_qty"] = available
            b["allotted_qty"] = available
            b["allotted_cones"] = int(b.custom_cone or 0)
            result.append(b)
            remaining_boxes -= 1
        return result

    if cone:
        # Allocate by cone count, calculate proportional weight
        result = []
        remaining_cones = cone
        for b in batches:
            if remaining_cones <= 0:
                break
            available_qty = _get_available_qty(b.name, b.batch_qty)
            if available_qty <= 0:
                continue
            batch_cones = int(b.custom_cone or 0)
            if batch_cones <= 0:
                continue
            available_cones = _get_available_cones(b.name, batch_cones)
            if available_cones <= 0:
                continue
            allotted_cones = min(available_cones, remaining_cones)
            # Proportional weight based on cones
            allotted_weight = flt(b.batch_qty) * allotted_cones / batch_cones
            b["available_qty"] = available_qty
            b["allotted_qty"] = flt(allotted_weight, 3)
            b["allotted_cones"] = allotted_cones
            b["available_cones"] = available_cones
            result.append(b)
            remaining_cones -= allotted_cones
        return result

    if not qty:
        result = []
        for b in batches:
            available = _get_available_qty(b.name, b.batch_qty)
            if available > 0:
                b["available_qty"] = available
                b["allotted_qty"] = available
                b["allotted_cones"] = int(b.custom_cone or 0)
                result.append(b)
        return result

    # Weight mode (MI1 2026-07-20): fetch ONLY complete batches. Skip
    # any batch whose full available qty would push the running total
    # over the requested weight — never partial-fetch a batch. Batches
    # are walked in their natural order (custom_supplier_batch_no asc
    # from the get_all above) so smaller subsequent batches still get
    # a chance to fill in if a bigger one had to be skipped.
    result = []
    total_weight = 0
    for b in batches:
        if total_weight >= qty:
            break
        available = _get_available_qty(b.name, b.batch_qty)
        if available <= 0:
            continue
        if total_weight + available > qty:
            # Full batch would exceed target — skip; do not partial-fetch.
            # To include this batch, user must raise the entered weight.
            continue
        b["available_qty"] = available
        b["allotted_qty"] = available
        b["allotted_cones"] = int(b.custom_cone or 0)
        result.append(b)
        total_weight += available

    return result


@frappe.whitelist()
def get_item_batch(batch):
    """Get batch details for a single batch."""
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
    }


@frappe.whitelist()
def get_container_details(container_no, transaction_type=None, with_stock=0):
    """Fetch unique lot_no + item combinations from every submitted
    Container doc whose `container_no` field matches the argument.

    MI1-I91 (Raj 2026-09-03): two optional narrowings for the HTY Sales
    Order lot popup, both off by default so the plain call is unchanged:
      * `transaction_type` — only Containers of that mode.
      * `with_stock`       — only (lot, item) pairs that still have at least
        one batch with a positive Serial-and-Batch-Bundle balance (reuses
        mhr.utilis.get_container_batches_with_stock). Zero-stock lots are
        dropped, as the ticket asks.

    MI1-I96 (Raj 2026-08-13): `with_stock` means available to BOOK — the
    balance on hand minus what open Sales Orders already hold against the
    batch (the rule _get_available_qty applies per batch, here in one
    query). A lot that exists against the container but is delivered or
    fully booked is not offered. Each returned row carries the lot's
    `available_qty`. The VFY "Sales Order Booking" popup passes
    with_stock=1 since MI1-I96; the HTY popup did since MI1-I91.

    MI1 2026-07-20 fix: the earlier implementation treated the argument
    as a doc NAME (`frappe.db.exists("Container", container_no)`), which
    fails for the real-world usage — Sales Order's custom_container_no
    is a plain Data field holding the container_no FIELD VALUE (e.g.
    `TRADING YARN`), not the doc name (e.g. `TRADING YARN-7893`). Now
    we resolve directly by field, so any user-typed container_no that
    matches at least one submitted Container returns its lots.
    """
    if not container_no:
        return []

    # Find all submitted Container docs whose container_no matches.
    container_filters = {"container_no": container_no, "docstatus": 1}
    if transaction_type:
        container_filters["transaction_type"] = transaction_type
    containers = frappe.get_all(
        "Container",
        filters=container_filters,
        fields=["name", "lot_no", "item"],
        order_by="creation desc",
    )
    if not containers:
        return []

    # For containers missing item, try to get from batches
    for c in containers:
        if not c.get("item"):
            batch_item = frappe.db.get_value(
                "Batch Items",
                {"parent": c.name},
                "item",
            )
            if batch_item:
                c["item"] = batch_item

    # Deduplicate by (lot_no, item)
    seen = set()
    unique = []
    for c in containers:
        key = (c.get("lot_no"), c.get("item"))
        if key not in seen:
            seen.add(key)
            unique.append({"lot_no": c.get("lot_no"), "item": c.get("item")})

    if cint(with_stock) and unique:
        # Local import: mhr.utilis is large and imports widely; keep the
        # dependency out of module load.
        from mhr.utilis import get_container_batches_with_stock

        stocked = get_container_batches_with_stock(container_no)
        if transaction_type and stocked:
            # The stock helper does not return the batch's mode; resolve it
            # here so a VFY batch that happens to share (lot, item) with an
            # HTY Container cannot keep a zero-stock HTY lot alive.
            in_mode = set(frappe.get_all(
                "Batch",
                filters={
                    "name": ["in", [b["name"] for b in stocked]],
                    "custom_transaction_type": transaction_type,
                },
                pluck="name",
            ))
            stocked = [b for b in stocked if b["name"] in in_mode]
        # MI1-I96: on hand minus open bookings, summed per (lot, item).
        booked = _booked_qty_by_batch([b["name"] for b in stocked])
        available_by_key = {}
        for b in stocked:
            avail = flt(b.get("batch_qty")) - booked.get(b["name"], 0.0)
            if avail > 0:
                key = (b.get("custom_lot_no"), b.get("item"))
                available_by_key[key] = available_by_key.get(key, 0.0) + avail
        unique = [
            dict(u, available_qty=flt(available_by_key[(u["lot_no"], u["item"])], 3))
            for u in unique
            if (u["lot_no"], u["item"]) in available_by_key
        ]

    return unique


def _booked_qty_by_batch(batch_names):
    """Open Sales Order bookings per batch — Σ(qty − delivered_qty) over
    submitted orders still to deliver — for many batches in one query.
    Same rule as _get_available_qty; that one stays per batch for the
    allocation loops, this one feeds the lot popup (MI1-I96)."""
    names = [n for n in batch_names if n]
    if not names:
        return {}
    rows = frappe.db.sql(
        """
        SELECT soi.custom_batch_no, COALESCE(SUM(soi.qty - soi.delivered_qty), 0)
        FROM `tabSales Order Item` soi
        JOIN `tabSales Order` so ON so.name = soi.parent
        WHERE soi.custom_batch_no IN %(names)s
          AND so.docstatus = 1
          AND so.status IN ('To Deliver and Bill', 'To Deliver', 'To Bill', 'Partially Delivered')
        GROUP BY soi.custom_batch_no
        """,
        {"names": names},
    )
    return {r[0]: flt(r[1]) for r in rows}


def _get_available_qty(batch_name, batch_qty):
    """Calculate available qty = batch stock - already booked in submitted SOs."""
    already_booked = flt(frappe.db.sql("""
        SELECT COALESCE(SUM(soi.qty - soi.delivered_qty), 0)
        FROM `tabSales Order Item` soi
        JOIN `tabSales Order` so ON so.name = soi.parent
        WHERE soi.custom_batch_no = %s
        AND so.docstatus = 1
        AND so.status IN ('To Deliver and Bill', 'To Deliver', 'To Bill', 'Partially Delivered')
    """, batch_name)[0][0])
    return flt(batch_qty) - already_booked


@frappe.whitelist()
def get_container_numbers(txt=""):
    """Return distinct container_no values from submitted Containers for autocomplete."""
    condition = ""
    if txt:
        condition = "AND container_no LIKE %(txt)s"

    data = frappe.db.sql(
        f"""SELECT DISTINCT container_no
        FROM `tabContainer`
        WHERE docstatus = 1 {condition}
        ORDER BY container_no ASC
        LIMIT 20""",
        {"txt": f"%{txt}%"} if txt else {},
        as_dict=True,
    )
    return [d.container_no for d in data if d.container_no]


def _get_available_cones(batch_name, batch_cones):
    """Calculate available cones = batch cones - already booked cones in submitted SOs."""
    already_booked = flt(frappe.db.sql("""
        SELECT COALESCE(SUM(soi.custom_cone), 0)
        FROM `tabSales Order Item` soi
        JOIN `tabSales Order` so ON so.name = soi.parent
        WHERE soi.custom_batch_no = %s
        AND so.docstatus = 1
        AND so.status IN ('To Deliver and Bill', 'To Deliver', 'To Bill', 'Partially Delivered')
    """, batch_name)[0][0])
    return int(batch_cones) - int(already_booked)
