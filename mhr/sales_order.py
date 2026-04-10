import frappe
from frappe.utils import flt


@frappe.whitelist()
def get_so_batches(item_code, container_no=None, lot_no=None, cone=0, qty=0, boxes=0):
    """Fetch batches for Sales Order based on item, container, lot and auto-split by cone, qty or boxes."""
    qty = flt(qty)
    cone = int(cone or 0)
    boxes = int(boxes or 0)
    filters = {"item": item_code, "batch_qty": (">", 0)}
    if container_no:
        filters["custom_container_no"] = container_no
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
        # Allocate by boxes and cones: pick N boxes, distribute cones across them
        result = []
        remaining_boxes = boxes
        remaining_cones = cone
        for b in batches:
            if remaining_boxes <= 0 or remaining_cones <= 0:
                break
            available = _get_available_qty(b.name, b.batch_qty)
            if available <= 0:
                continue
            batch_cones = int(b.custom_cone or 0)
            if batch_cones <= 0:
                continue
            available_cones = _get_available_cones(b.name, batch_cones)
            if available_cones <= 0:
                continue
            allotted_cones = min(available_cones, remaining_cones)
            b["available_qty"] = available
            b["allotted_qty"] = available
            b["allotted_cones"] = allotted_cones
            result.append(b)
            remaining_boxes -= 1
            remaining_cones -= allotted_cones
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

    # Weight mode: pick full batches (no partial) until total >= requested weight
    # Sort by weight descending to pick fewest batches
    batches.sort(key=lambda b: flt(b.batch_qty), reverse=True)
    result = []
    total_weight = 0
    for b in batches:
        if total_weight >= qty:
            break
        available = _get_available_qty(b.name, b.batch_qty)
        if available <= 0:
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
def get_container_details(container_no):
    """Fetch unique lot_no and item combinations from Container for a given container_no."""
    containers = frappe.get_all(
        "Container",
        filters={"container_no": container_no, "docstatus": 1},
        fields=["lot_no", "item"],
        order_by="creation desc",
    )
    if not containers:
        return []

    # Deduplicate by (lot_no, item)
    seen = set()
    unique = []
    for c in containers:
        key = (c.get("lot_no"), c.get("item"))
        if key not in seen:
            seen.add(key)
            unique.append({"lot_no": c.get("lot_no"), "item": c.get("item")})

    return unique


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
