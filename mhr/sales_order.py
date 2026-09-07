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
    """Open Sales Order bookings per batch for the lot popup (MI1-I96) — the
    same Sales-Order-wise rule as _get_available_qty (MI1-I120 revision),
    for many batches in one pass."""
    from mhr.utilis import effective_booking_by_batch

    names = [n for n in batch_names if n]
    if not names:
        return {}
    return {b: flt(v["qty"]) for b, v in effective_booking_by_batch(names).items() if flt(v["qty"]) > 0}


def _get_available_qty(batch_name, batch_qty):
    """Available to book = batch stock − what open Sales Orders still hold.

    MI1-I120 revision (Raj 2026-09-05): booking is released Sales-Order-wise —
    effective booking = ordered − delivered (any batches), floored at zero,
    applied down the order's rows — via mhr.utilis.effective_booking_by_batch.
    """
    from mhr.utilis import effective_booking_by_batch

    booked = effective_booking_by_batch([batch_name]).get(batch_name, {}).get("qty", 0.0)
    return flt(batch_qty) - flt(booked)


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
    """Available cones = batch cones − cones still booked, under the same
    Sales-Order-wise release rule as _get_available_qty (MI1-I120 revision)."""
    from mhr.utilis import effective_booking_by_batch

    booked = effective_booking_by_batch([batch_name]).get(batch_name, {}).get("cones", 0.0)
    return int(batch_cones) - int(round(flt(booked)))


# ---------------------------------------------------------------------------
# MI1-I128 (Rohit 2026-09-07): the Sales Order's Set Source Warehouse must be
# the warehouse the selected Container was inwarded to.
# ---------------------------------------------------------------------------
# "Inward warehouse" is Container.set_warehouse (the Accepted Warehouse the
# inward Purchase Receipt posted to — MI1-I103), read per (container, lot);
# when a legacy Container never had it filled, the Purchase Receipt that names
# the container is asked the same way heal_container_accepted_warehouse does.
# Stock that was moved since (MI1-I125: a Material Transfer to Vadod - MC) is
# no longer at the inward warehouse, so a warehouse that currently HOLDS the
# container's batches is accepted as well — refusing it would make the order
# impossible to raise anywhere. Both come back from
# get_container_source_warehouse, which the two lot pickers call to fill a
# blank Set Source Warehouse, and validate_so_source_warehouse enforces on
# submit. An order without a Container is untouched.


def _container_inward_warehouses(container_no, lot_no=None):
    """Distinct inward warehouses of the submitted Container doc(s) for this
    container (and lot, when given): Container.set_warehouse, else the
    warehouse its non-return Purchase Receipts posted to."""
    filters = {"container_no": container_no, "docstatus": 1}
    if lot_no:
        filters["lot_no"] = lot_no
    rows = frappe.get_all("Container", filters=filters, fields=["name", "set_warehouse"])
    if not rows and lot_no:
        rows = frappe.get_all("Container", filters={"container_no": container_no, "docstatus": 1},
                              fields=["name", "set_warehouse"])
    inward = sorted({r.set_warehouse for r in rows if r.set_warehouse})
    if inward:
        return inward
    receipts = frappe.db.sql(
        """
        SELECT DISTINCT pri.warehouse
        FROM `tabPurchase Receipt` pr
        INNER JOIN `tabPurchase Receipt Item` pri ON pri.parent = pr.name
        WHERE pr.custom_container_no = %s AND pr.docstatus = 1
          AND IFNULL(pr.is_return, 0) = 0 AND IFNULL(pri.warehouse, '') != ''
        """,
        (container_no,),
    )
    return sorted({r[0] for r in receipts if r[0]})


def _container_live_warehouses(container_no, lot_no=None):
    """Warehouses currently holding a positive Serial and Batch Bundle balance
    of the container's (lot's) batches, largest first."""
    lot_sql = " AND b.custom_lot_no = %(lot_no)s" if lot_no else ""
    rows = frappe.db.sql(
        f"""
        SELECT sbb.warehouse, SUM(sbe.qty) AS qty
        FROM `tabBatch` b
        INNER JOIN `tabSerial and Batch Entry` sbe ON sbe.batch_no = b.name
        INNER JOIN `tabSerial and Batch Bundle` sbb ON sbb.name = sbe.parent
        WHERE b.custom_container_no = %(container_no)s{lot_sql}
          AND sbb.docstatus = 1 AND sbb.is_cancelled = 0
          AND sbb.type_of_transaction IN ('Inward', 'Outward')
        GROUP BY sbb.warehouse
        HAVING SUM(sbe.qty) > 0
        ORDER BY qty DESC
        """,
        {"container_no": container_no, "lot_no": lot_no},
    )
    return [r[0] for r in rows if r[0]]


@frappe.whitelist()
def get_container_source_warehouse(container_no, lot_no=None):
    """{inward: [..], live: [..], suggested: str|None} for the lot pickers:
    `suggested` is the inward warehouse when there is exactly one, else the
    warehouse holding most of the stock."""
    container_no = (container_no or "").strip()
    lot_no = (lot_no or "").strip() or None
    if not container_no:
        return {"inward": [], "live": [], "suggested": None}
    inward = _container_inward_warehouses(container_no, lot_no)
    live = _container_live_warehouses(container_no, lot_no)
    suggested = inward[0] if len(inward) == 1 else (live[0] if live else (inward[0] if inward else None))
    return {"inward": inward, "live": live, "suggested": suggested}


def validate_so_source_warehouse(doc, method=None):
    """before_submit: Set Source Warehouse (and every row's warehouse) must be
    where the Container was inwarded, or where its stock now is."""
    container_no = (doc.get("custom_container_no") or "").strip()
    if not container_no:
        return
    lot_no = (doc.get("custom_lot_no") or "").strip() or None
    inward = _container_inward_warehouses(container_no, lot_no)
    live = _container_live_warehouses(container_no, lot_no)
    allowed = set(inward) | set(live)
    label = frappe._(" / ").join(inward) if inward else None

    def _expected():
        parts = []
        if inward:
            parts.append(frappe._("inwarded to {0}").format(frappe.bold(label)))
        if live:
            parts.append(frappe._("stock currently in {0}").format(frappe.bold(", ".join(live))))
        return frappe._(" — ").join(parts)

    if not doc.get("set_warehouse"):
        frappe.throw(
            frappe._("Set Source Warehouse is mandatory when a Container is selected. Container {0} was {1}.").format(
                frappe.bold(container_no), _expected() or frappe._("not found in stock")
            ),
            title=frappe._("Source Warehouse Required"),
        )
    if not allowed:
        # Nothing known about where this container lives (no inward record,
        # no stock): nothing to compare against; the availability check on
        # validate already decides what can be booked.
        return
    if doc.set_warehouse not in allowed:
        frappe.throw(
            frappe._("Set Source Warehouse {0} does not match Container {1}, which was {2}.").format(
                frappe.bold(doc.set_warehouse), frappe.bold(container_no), _expected()
            ),
            title=frappe._("Source Warehouse Mismatch"),
        )
    for row in doc.get("items") or []:
        if row.get("warehouse") and row.warehouse not in allowed:
            frappe.throw(
                frappe._("Row {0}: Delivery Warehouse {1} does not match Container {2}, which was {3}.").format(
                    row.idx, frappe.bold(row.warehouse), frappe.bold(container_no), _expected()
                ),
                title=frappe._("Source Warehouse Mismatch"),
            )
