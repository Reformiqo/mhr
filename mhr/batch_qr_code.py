import frappe
# import segno
@frappe.whitelist()
def set_si_qrcode(doc, method=None):
    pass
# relaod the purchase receipt doctype  f a specific
frappe.reload_doc("stock", "doctype", "purchase_receipt")
    
    # img = segno.make_qr("Hello, World")
    
    # # Define the file path
    # file_path = f"/home/frappe/frappe-bench/sites/sona.erpera.io/public/files/{doc.name}.png"
    
    # # Save the image to the file
    # img.save(file_path, scale=50)  

    # # Read the image file from the system
    # with open(file_path, "rb") as file:
    #     file_content = file.read()


    # # Create a new file document in Frappe
    # file_doc = frappe.get_doc({
    #     "doctype": "File",
    #     "file_name": f"{doc.name}.png",
    #     "is_private": 0,  # 0 for public, 1 for private
    #     "content": file_content,
        
    # })

    # file_doc.insert()
    # frappe.db.set_value("Batch", doc.name, "custom_qr_image", file_doc.file_url)
    # frappe.db.commit()
    # return file_doc.file_url