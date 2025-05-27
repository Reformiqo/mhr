import frappe
from frappe.utils import flt
		
def get_items(container):
    
    
    items = []

    # Iterate over the batches in the Container document
    for batch in container.batches:
        # Check if the item is already in the list
        existing_item = next((item for item in items if item["item"] == batch.item), None)
        if existing_item:
            # If the item exists, increase the batch_qty
            existing_item["batch_qty"] += float(batch.qty)
        else:
            # If the item does not exist, add it to the list
            items.append({
                "item": batch.item,
                "batch_qty": float(batch.qty),
                "stock_uom": batch.uom,
                "name": batch.batch_id,
            })
    return items
def get_item_batches(container, item_code):
    items = get_items(container)
    batches = []
    for item in items:
        for batch in container.batches:
            if item["item"] == item_code:
                batches.append({
                "batch_id": batch.batch_id,
                "qty": float(batch.qty),
                "uom": batch.uom,
                "cone": batch.cone,
                "supplier_batch_no": batch.supplier_batch_no,
                "warehouse": "Finished Goods - MC"

                })
    return batches
def create_serial_and_batch_bundle(container, item_code, transaction_type):
    try:
        batches  = get_item_batches(container, item_code)
        sb_bundle = frappe.new_doc("Serial and Batch Bundle")
        sb_bundle.company = "Meher Creations"
        sb_bundle.type_of_transaction = transaction_type
        sb_bundle.has_batch_no = 1
        sb_bundle.has_serial_no = 0
        sb_bundle.item_code = item_code
        sb_bundle.item_name = item_code
        sb_bundle.voucher_type = "Purchase Receipt"
        sb_bundle.warehouse = "Finished Goods - MC"
        
        for batch in batches:
            sb_bundle.append("entries", {
                "batch_no": batch['batch_id'],
                "qty": batch['qty'],
                "uom": batch['uom'],
                "cone": batch['cone'],
                "supplier_batch_no": batch['supplier_batch_no'],
                "warehouse": "Finished Goods - MC",

            })
            
        sb_bundle.save()
        frappe.db.commit()
        return sb_bundle.name
    except Exception as e:
        frappe.db.rollback()
        frappe.log_error(frappe.get_traceback(), "create_serial_and_batch_bundle")
        return {"message": "Failed to create Serial and Batch Bundle", "error": str(e)}

@frappe.whitelist()
def enqueue_create_receipts():
    frappe.enqueue("mhr.job.create_receipts", queue="long")
    return "receipts created successfully"

@frappe.whitelist()
def create_receipts():
    containers = frappe.db.sql("SELECT name FROM `tabContainer` WHERE name NOT IN (SELECT custom_container_no FROM `tabPurchase Receipt`) AND docstatus = 1")
    for container in containers:
        doc = frappe.get_doc("Container", container)
        doc.create_purchase_receipt(doc)
        frappe.db.commit()

@frappe.whitelist()
def create_purchase_receipt(container, is_return=0, pr=None):
    items = get_items(container)

    # Create a new Purchase Receipt document
    purchase_receipt = frappe.new_doc("Purchase Receipt")
    purchase_receipt.supplier = container.supplier
    purchase_receipt.posting_date = container.posting_date
    purchase_receipt.custom_container_no = container.name
    purchase_receipt.custom_total_batches = len(container.batches)
    purchase_receipt.custom_lot_number = container.lot_no
    purchase_receipt.custom_lusture = container.lusture
    purchase_receipt.custom_glue = container.glue
    purchase_receipt.custom_grade = container.grade
    purchase_receipt.custom_pulp = container.pulp
    purchase_receipt.custom_fsc = container.fsc
    purchase_receipt.custom_merge_no = container.merge_no
    purchase_receipt.items = []

    # Add items to the Purchase Receipt
    if is_return == 1:
        purchase_receipt.is_return = 1
        purchase_receipt.return_against = pr
        for item in items:
            serial_and_batch_bundle = create_serial_and_batch_bundle(container, item["item"], "Outward")
            purchase_receipt.append("items", {
                "item_code": item["item"],
                "item_name": item["item"],
                "qty": -(flt(item["batch_qty"])),
                "stock_uom": item["stock_uom"],
                "warehouse": "Finished Goods - MC",
                "allow_zero_valuation_rate": 1,
                "rate": 100,
                "price_list_rate": 100,
                "received_qty": -(flt(item["batch_qty"])),
                "conversion_factor": 1,
                "use_serial_batch_fields": 0,
                "serial_and_batch_bundle": serial_and_batch_bundle
            })
    else:
        for item in items:
            serial_and_batch_bundle = create_serial_and_batch_bundle(container, item["item"], "Inward")
            purchase_receipt.append("items", {
                "item_code": item["item"],
                "item_name": item["item"],
                "qty": item["batch_qty"],
                "stock_uom": item["stock_uom"],
                "warehouse": "Finished Goods - MC",
                "allow_zero_valuation_rate": 1,
                "rate": 100,
                "price_list_rate": 100,
                "received_qty": item["batch_qty"],
                "conversion_factor": 1,
                "use_serial_batch_fields": 0,
                "serial_and_batch_bundle": serial_and_batch_bundle
            })
    

    # Save and submit the Purchase Receipt
    try:
        purchase_receipt.save()
        purchase_receipt.submit()
        frappe.db.commit()
        return purchase_receipt.name
        
    except Exception as e:
        frappe.db.rollback()
        frappe.log_error(frappe.get_traceback(), "create_purchase_receipt")
        frappe.msgprint({"message": "Failed to create Purchase Receipt", "error": str(e)})
