import frappe
from frappe.utils import cint

@frappe.whitelist()
def validate_batch(doc, method=None):
    for item in doc.items:
        if item.batch_no:
            batch = frappe.get_doc('Batch', item.batch_no)
            if doc.custom_lusture != batch.custom_lusture:
                frappe.throw(f'Lusture is not The same with the lusture in Batch {batch.name}')
            if doc.custom_grade != batch.custom_grade:
                frappe.throw(f'Grade is not The same with the grade in Batch {batch.name}')
            if doc.custom_glue != batch.custom_glue:
                frappe.throw(f'Glue is not The same with the glue in Batch {batch.name}')
            if doc.custom_pulp != batch.custom_pulp:
                frappe.throw(f'Pulp is not The same with the pulp in Batch {batch.name}')
            if doc.custom_fsc != batch.custom_fsc:
                frappe.throw(f'FSC is not The same with the FSC in Batch {batch.name}')
            if item.supplier_batch_no != batch.custom_supplier_batch_no:
                frappe.throw(f'Supplier Batch No is not The same with the supplier batch no in Batch {batch.name}')
            if item.custom_lot_no != batch.custom_lot_no:
                frappe.throw(f'Lot No is not The same with the lot no in Batch {batch.name}')
            if item.custom_container_no != batch.custom_container_no:
                frappe.throw(f'Container no is not The same with the container no in Batch {batch.name}')
            if item.cone != batch.custom_cone:
                frappe.throw(f'Cone is not The same with the cone in Batch {batch.name}')




@frappe.whitelist()
def get_item_batch(batch):
    if not frappe.db.exists('Batch', batch):
        return {'error': 'Batch not found'}

    
    item = frappe.get_doc("Batch", batch)
    return {
        'item_code': item.item,
        'item_name': item.item_name,
        'qty': item.batch_qty,
        'uom': item.stock_uom,
        'batch_no': item.name,
        'supplier_batch_no':item.custom_supplier_batch_no,
        "cone": item.custom_cone,
        'container_no':item.custom_container_no,
        'lot_no': item.custom_lot_no
    }

@frappe.whitelist()
def update_item_batch(doc, method=None):
    for item in doc.items:
        remaining_cone = cint(frappe.db.get_value("Batch", item.batch_no, "custom_cone")) - cint(item.qty)
        frappe.db.set_value("Batch", item.batch_no, "custom_cone", remaining_cone)
        frappe.db.commit()


@frappe.whitelist()
def get_batches(container_no, lot_no):
	# frappe.msgprint("container_no: {0} lot_no: {1}".format(container_no, lot_no))
	batches = frappe.get_all("Batch", 
                          filters={"custom_container_no": container_no, "custom_lot_no": lot_no},
                          fields=["name", 
                                  "item", 
                                  "item_name", 
                                  "batch_qty", 
                                  "stock_uom", 
                                  "custom_supplier_batch_no", 
                                  "custom_cone", 
                                  'custom_lusture',
                                  'custom_grade',
                                  'custom_glue',
                                  'custom_pulp',
                                  'custom_fsc',
                                  ]
                                  )
	return batches

@frappe.whitelist()
def get_lot_nos(container_no):
    lot_nos = frappe.get_all("Batch", 
                          filters={"custom_container_no": container_no},
                          fields=["custom_lot_no"])
    return lot_nos[0].get('custom_lot_no') if lot_nos else None
@frappe.whitelist()
def get_total_batches(container_no, lot_no):
    batches = get_batches(container_no, lot_no)
    return len(batches)



@frappe.whitelist()
def get_items():
    # Fetch the last created Container document
    doc = frappe.get_last_doc("Container")
    items = []

    # Iterate over the batches in the Container document
    for batch in doc.batches:
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
@frappe.whitelist()
def get_item_batches(item_code):
    items = get_items()
    batches = []
    doc = frappe.get_last_doc("Container")
    for item in items:
        for batch in doc.batches:
            if item["item"] == item_code:
                batches.append({
                "batch_id": batch.batch_id,
                "qty": float(batch.qty),
                "uom": batch.uom,
                "cone": batch.cone,
                "supplier_batch_no": batch.supplier_batch_no,

                })
    return batches
@frappe.whitelist()
def create_serial_and_batch_bundle(item_code):
    try:
        batches  = get_item_batches(item_code)
        sb_bundle = frappe.new_doc("Serial and Batch Bundle")
        sb_bundle.company = "Meher Creation"
        sb_bundle.type_of_transaction = "Inward"
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
def create_purchase_receipt():
    items = get_items()
    doc = frappe.get_last_doc("Container")

    # Create a new Purchase Receipt document
    purchase_receipt = frappe.new_doc("Purchase Receipt")
    purchase_receipt.supplier = doc.supplier
    purchase_receipt.posting_date = doc.posting_date
    purchase_receipt.custom_total_batches = len(items)
    purchase_receipt.items = []

    # Add items to the Purchase Receipt
    for item in items:
        serial_and_batch_bundle = create_serial_and_batch_bundle(item["item"])
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
        
        return {"message": "Purchase Receipt created successfully", "purchase_receipt": purchase_receipt.name}
    except Exception as e:
        frappe.db.rollback()
        frappe.log_error(frappe.get_traceback(), "create_purchase_receipt")
        return {"message": "Failed to create Purchase Receipt", "error": str(e)}

# def create_purchase_receipt(items, supplier):
#     try:
#         pr = frappe.new_doc("Purchase Receipt")
        # pr.supplier = "Jilin Chemical Fiber Stock Co. Ltd"
        # pr.posting_date = "2024-05-01"
        # pr.set_posting_time = "12:00:00"
        # pr.custom_container_no = "MCJC-369"
        # pr.custom_lot_number = "29102023"
        # for item in items:
        #     pr.append("items", {
        #         "item_code": item.get('item'),
        #         "item_name": item.get('item_name'),
                # "qty": item.get('batch_qty'),
                # "uom": item.get('stock_uom'),
                # "rate": 100,
                # "price_list_rate": 100,
                # "received_qty": item.get('batch_qty'),
                # "conversion_factor": 1,
                # "warehouse": "Finished Goods - MC",  
                # "use_serial_batch_fields": 1,
                # "batch_no": item.get('name'),  
#             })
#         pr.flags.ignore_permissions = True
#         pr.insert()
#         pr.submit()
#         frappe.db.commit()
#         return f"Purchase Receipt {pr.name} created successfully"
#     except Exception as e:
#         frappe.throw(f"Error creating Purchase Receipt: {e}")
# @frappe.whitelist()
# def get_purchase_items():
#     container_no = "MCJC-369"
#     lot_no = "29102023"
#     batches = get_batches(container_no, lot_no)
#     return batches


@frappe.whitelist()
def create_batch():
    # get the last batch number
    last_batch = frappe.get_last_doc("Batch")
    last_batch_name = last_batch.name
    batch = last_batch_name[3:]
    try:
        batch = frappe.new_doc("Batch")
        batch.item = "120D/30F"
        batch.item_name = "120D/30F"
        batch.batch_qty = 31.6
        batch.stock_uom = "Meter"
        batch.custom_supplier_batch_no = "4825"
        batch.custom_container_no = "MCJC-369"
        batch.custom_lot_no = "29102023"
        batch.custom_lusture = "Dull"
        batch.custom_grade = "AA EVEN"
        batch.custom_glue = "Centrifugal"
        batch.custom_pulp = "Wood"
        batch.custom_fsc = "Mix"
        batch.insert()
        frappe.db.commit()
        return f"Batch {batch.name} created successfully"
    except Exception as e:
        frappe.throw(f"Error creating Batch: {e}")
