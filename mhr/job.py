import frappe
from frappe.utils import flt
from frappe.model.utils.user_settings import save, get
import json
		
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



@frappe.whitelist()
def update_gridview_settings():
    try:
        doctype_name = "Delivery Note"
        
        # Define only the GridView settings to update
        gridview_settings = {
            "GridView": {
                "Delivery Note Item": [
                    {"fieldname": "item_code", "columns": 2},
                    {"fieldname": "qty", "columns": 2},
                    {"fieldname": "warehouse", "columns": 2}
                ]
            }
        }
        
        # Get all active users
        users = frappe.get_all("User", 
                              filters={"enabled": 1, "user_type": "System User"}, 
                              fields=["name"])
        
        # Store original user session
        original_user = frappe.session.user
        success_count = 0
        error_count = 0
        
        # Update GridView settings for each user
        for user in users:
            try:
                # Set session user to the current user
                frappe.session.user = user.name
                
                # Get existing user settings
                existing_settings_json = frappe.db.sql(
                    """SELECT data FROM `__UserSettings` 
                       WHERE `user`=%s AND `doctype`=%s""",
                    (user.name, doctype_name)
                )
                
                if existing_settings_json and existing_settings_json[0][0]:
                    # Parse existing settings
                    existing_settings = json.loads(existing_settings_json[0][0])
                    if isinstance(existing_settings, str):
                        existing_settings = {}
                else:
                    # No existing settings, create new
                    existing_settings = {}
                
                # Update only the GridView part
                existing_settings.update(gridview_settings)
                
                # Convert to JSON string
                updated_settings_json = json.dumps(existing_settings)
                
                # Save updated settings
                save(doctype_name, updated_settings_json)
                
                success_count += 1
                
            except Exception as user_error:
                error_count += 1
                frappe.log_error(
                    f"Error updating GridView settings for user {user.name}: {str(user_error)}", 
                    "GridView Settings Update Error"
                )
        
        # Restore original user session
        frappe.session.user = original_user
        
        # Commit all changes
        frappe.db.commit()
        
        return {
            "message": f"GridView settings updated successfully for {success_count} users. Errors: {error_count}",
            "success_count": success_count,
            "error_count": error_count
        }
        
    except Exception as e:
        frappe.db.rollback()
        frappe.log_error(frappe.get_traceback(), "Update GridView Settings")
        return {"message": f"Failed to update GridView settings: {str(e)}"}


# Alternative method using direct database operations (more efficient)
@frappe.whitelist()
def update_gridview_settings_bulk():
    try:
        doctype_name = "Delivery Note"
        
        # Define only the GridView settings to update
        gridview_settings = {
            "GridView": {
                "Delivery Note Item": [
                    {"fieldname": "item_code", "columns": 2},
                    {"fieldname": "qty", "columns": 2},
                    {"fieldname": "warehouse", "columns": 2}
                ]
            }
        }
        
        # Get all active users
        users = frappe.get_all("User", 
                              filters={"enabled": 1, "user_type": "System User"}, 
                              fields=["name"])
        
        success_count = 0
        error_count = 0
        
        for user in users:
            try:
                # Get existing user settings
                existing_settings = frappe.db.sql(
                    """SELECT data FROM `__UserSettings` 
                       WHERE `user`=%s AND `doctype`=%s""",
                    (user.name, doctype_name)
                )
                
                if existing_settings and existing_settings[0][0]:
                    # Parse existing settings and merge with GridView update
                    current_data = json.loads(existing_settings[0][0] or "{}")
                    if isinstance(current_data, str):
                        current_data = {}
                    
                    # Update only GridView settings
                    current_data.update(gridview_settings)
                    final_data = json.dumps(current_data)
                    
                    # Update existing record
                    frappe.db.sql(
                        """UPDATE `__UserSettings` SET `data`=%s 
                           WHERE `user`=%s AND `doctype`=%s""",
                        (final_data, user.name, doctype_name)
                    )
                else:
                    # Insert new record with only GridView settings
                    final_data = json.dumps(gridview_settings)
                    frappe.db.sql(
                        """INSERT INTO `__UserSettings` (`user`, `doctype`, `data`) 
                           VALUES (%s, %s, %s)""",
                        (user.name, doctype_name, final_data)
                    )
                
                # Update cache
                cache_key = f"{doctype_name}::{user.name}"
                frappe.cache.hset("_user_settings", cache_key, final_data)
                
                success_count += 1
                
            except Exception as user_error:
                error_count += 1
                frappe.log_error(
                    f"Error updating GridView settings for user {user.name}: {str(user_error)}", 
                    "Bulk GridView Settings Update Error"
                )
        
        # Commit all changes
        frappe.db.commit()
        
        return {
            "message": f"GridView settings updated successfully for {success_count} users. Errors: {error_count}",
            "success_count": success_count,
            "error_count": error_count
        }
        
    except Exception as e:
        frappe.db.rollback()
        frappe.log_error(frappe.get_traceback(), "Bulk Update GridView Settings")
        return {"message": f"Failed to update GridView settings: {str(e)}"}


# Method to update GridView for specific users only
@frappe.whitelist()
def update_gridview_for_specific_users(user_list=None):
    """
    Update GridView settings for specific users
    
    Args:
        user_list (list): List of user emails. If None, updates for all users
    """
    try:
        doctype_name = "Delivery Note"
        
        # Define only the GridView settings to update
        gridview_settings = {
            "GridView": {
                "Delivery Note Item": [
                    {"fieldname": "item_code", "columns": 2},
                    {"fieldname": "warehouse", "columns": 2}
                ]
            }
        }
        
        # Get users based on provided list or all users
        if user_list:
            # Convert string to list if needed
            if isinstance(user_list, str):
                user_list = json.loads(user_list)
            
            users = []
            for user_email in user_list:
                if frappe.db.exists("User", {"name": user_email, "enabled": 1}):
                    users.append({"name": user_email})
        else:
            users = frappe.get_all("User", 
                                  filters={"enabled": 1, "user_type": "System User"}, 
                                  fields=["name"])
        
        success_count = 0
        error_count = 0
        
        for user in users:
            try:
                # Get existing user settings
                existing_settings = frappe.db.sql(
                    """SELECT data FROM `__UserSettings` 
                       WHERE `user`=%s AND `doctype`=%s""",
                    (user["name"], doctype_name)
                )
                
                if existing_settings and existing_settings[0][0]:
                    # Parse and update existing settings
                    current_data = json.loads(existing_settings[0][0] or "{}")
                    if isinstance(current_data, str):
                        current_data = {}
                    current_data.update(gridview_settings)
                    final_data = json.dumps(current_data)
                else:
                    # Create new settings with GridView only
                    final_data = json.dumps(gridview_settings)
                
                # Update or insert using upsert
                frappe.db.multisql(
                    {
                        "mariadb": """INSERT INTO `__UserSettings`(`user`, `doctype`, `data`)
                                     VALUES (%s, %s, %s)
                                     ON DUPLICATE KEY UPDATE `data`=%s""",
                        "postgres": """INSERT INTO `__UserSettings` (`user`, `doctype`, `data`)
                                      VALUES (%s, %s, %s)
                                      ON CONFLICT ("user", "doctype") DO UPDATE SET `data`=%s""",
                    },
                    (user["name"], doctype_name, final_data, final_data)
                )
                
                # Update cache
                cache_key = f"{doctype_name}::{user['name']}"
                frappe.cache.hset("_user_settings", cache_key, final_data)
                
                success_count += 1
                
            except Exception as user_error:
                error_count += 1
                frappe.log_error(
                    f"Error updating GridView settings for user {user['name']}: {str(user_error)}", 
                    "Specific GridView Settings Update Error"
                )
        
        # Commit all changes
        frappe.db.commit()
        
        return {
            "message": f"GridView settings updated successfully for {success_count} users. Errors: {error_count}",
            "success_count": success_count,
            "error_count": error_count,
            "users_processed": len(users)
        }
        
    except Exception as e:
        frappe.db.rollback()
        frappe.log_error(frappe.get_traceback(), "Update GridView Settings for Specific Users")
        return {"message": f"Failed to update GridView settings: {str(e)}"}
    
@frappe.whitelist()    
def get_child_tables(doctype_name):
    child_tables = frappe.get_all(
        "DocField",
        filters={
            "parent": doctype_name,
            "parenttype": "DocType",
            "fieldtype": "Table"
        },
        fields=["fieldname", "label", "options"]
    )
    return child_tables

@frappe.whitelist()
def get_meta(doctype):
    return frappe.get_meta(doctype)
@frappe.whitelist()
def get_user_settings(doctype, user):
    user_settings = frappe.cache.hget("_user_settings", f"{doctype}::{user}")

    if user_settings is None:
        result = frappe.db.sql(
            """SELECT data FROM `__UserSettings`
            WHERE `user` = %s AND `doctype` = %s""",
            (user, doctype),
        )
        user_settings = result[0][0] if result else "{}"

    # Ensure it's a dict
    try:
        data = frappe.parse_json(user_settings)
    except Exception:
        data = {}

    return frappe._dict(data)



@frappe.whitelist()
def create_purchase_receipt_for_container():
    # gett all the containers that are not in the purchase receipt using sql 
    containers = frappe.db.sql("SELECT name FROM `tabContainer` WHERE name NOT IN (SELECT custom_container_no FROM `tabPurchase Receipt`) AND docstatus = 1")
    for container in containers:
        doc = frappe.get_doc("Container", container)
        doc.create_purchase_receipt(doc)
        frappe.db.commit()


@frappe.whitelist()
def enqueue_create_purchase_receipt_for_container():
    frappe.enqueue("mhr.job.create_purchase_receipt_for_container", queue="long")
    return "purchase receipts created successfully"



                    

@frappe.whitelist()
def enqueue_delete_containers():
    frappe.enqueue("mhr.job.delete_containers", queue="long")
    return "containers deleted successfully"