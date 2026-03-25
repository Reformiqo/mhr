import frappe
from frappe.utils import flt


@frappe.whitelist()
def get_so_batches(item_code, container_no=None, lot_no=None, qty=0):
    """Fetch batches for Sales Order based on item, container, lot and auto-split by qty."""
    qty = flt(qty)
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

    if not qty:
        result = []
        for b in batches:
            available = _get_available_qty(b.name, b.batch_qty)
            if available > 0:
                b["available_qty"] = available
                b["allotted_qty"] = available
                result.append(b)
        return result

    # Auto-split: fill batches until requested qty is met
    result = []
    remaining = qty
    for b in batches:
        if remaining <= 0:
            break
        available = _get_available_qty(b.name, b.batch_qty)
        if available <= 0:
            continue
        allotted = min(available, remaining)
        b["available_qty"] = available
        b["allotted_qty"] = allotted
        result.append(b)
        remaining -= allotted

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
