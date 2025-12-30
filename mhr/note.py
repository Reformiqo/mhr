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

    # Check if at least one filter is applied
    if filters:
        batches = frappe.get_all("Batch", filters=filters, fields=["name", "item", "item_name", "batch_qty", "stock_uom", "custom_supplier_batch_no", "custom_cone", "custom_lusture", "custom_grade", "custom_glue", "custom_pulp", "custom_fsc"], limit=limit)
        return batches
    else:
        return []

