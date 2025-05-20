import frappe
from frappe import _
from frappe.utils import cint
from frappe.utils.print_format import download_pdf, download_multi_pdf
import json


@frappe.whitelist()
def update_stock_entry(doc, method=None):
    total_cone = 0
    total_qty = 0
    for item in doc.items:
        total_cone += cint(item.custom_cone)
        total_qty += cint(item.qty)
    doc.custom_total_cone = total_cone
    doc.custom_total_qty = total_qty


@frappe.whitelist()
def send_email_after_submit(doc, method=None):
    frappe.sendmail(
        recipients=["", ""],
        subject="Container Submitted",
        message="Container has been submitted successfully",
    )


@frappe.whitelist()
def update_cone_value():
    # update total cone value of the delivery note based on the items and an dcone vlue
    frappe.db.sql(
        """
        UPDATE `tabDelivery Note`
        SET custom_total_cone = (
            SELECT SUM(custom_cone)
            FROM `tabDelivery Note Item`
            WHERE parent = `tabDelivery Note`.name
        )
    """
    )
    frappe.db.commit()

    frappe.db.commit()


@frappe.whitelist()
def set_total_cone(doc, method=None):
    total_cone = 0
    for item in doc.items:
        total_cone += cint(item.custom_cone)
    doc.custom_total_cone = total_cone


@frappe.whitelist()
def same_container():
    containers = frappe.get_all("Container", fields=["*"])
    data = []
    same_container = []
    for container in containers:
        # //check if container_no is the same
        if container.container_no in same_container:
            continue
        same_container.append(container.container_no)
    return same_container


@frappe.whitelist()
def get_total_closing(container):
    con = frappe.get_doc("Container", container)
    total_closing = 0
    for batch in con.batches:
        total_closing += cint(batch.qty)
    return total_closing


@frappe.whitelist()
def validate_batch(doc, method=None):
    # if frappe.db.exists("Delivery Note", doc.challan_number):
    #     frappe.throw(
    #         f"Delivery Note {doc.challan_number} already exists. Please use a different challan number."
    #     )
    doc.custom_item_length = len(doc.items)
    for item in doc.items:
        if item.batch_no:
            batch = frappe.get_doc("Batch", item.batch_no)

            # Convert all fields to lower case for case-insensitive comparison
            doc_lusture = doc.custom_lusture.lower() if doc.custom_lusture else ""
            batch_lusture = batch.custom_lusture.lower() if batch.custom_lusture else ""
            if doc_lusture != batch_lusture:
                frappe.throw(
                    f"Lusture is not the same as the lusture in Batch {batch.name}"
                )

            doc_grade = doc.custom_grade.lower()
            batch_grade = batch.custom_grade.lower()
            if doc_grade != batch_grade:
                frappe.throw(
                    f"Grade is not the same as the grade in Batch {batch.name}"
                )

            doc_glue = doc.custom_glue.lower() if doc.custom_glue else ""
            batch_glue = batch.custom_glue.lower() if batch.custom_glue else ""
            if doc_glue != batch_glue:
                frappe.throw(f"Glue is not the same as the glue in Batch {batch.name}")

            doc_pulp = doc.custom_pulp.lower() if doc.custom_pulp else ""
            batch_pulp = batch.custom_pulp.lower() if batch.custom_pulp else ""
            if doc_pulp != batch_pulp:
                frappe.throw(f"Pulp is not the same as the pulp in Batch {batch.name}")

            doc_fsc = doc.custom_fsc.lower() if doc.custom_fsc else ""
            batch_fsc = batch.custom_fsc.lower() if batch.custom_fsc else ""
            if doc_fsc != batch_fsc:
                frappe.throw(f"FSC is not the same as the FSC in Batch {batch.name}")

            doc_lot_no = item.custom_lot_no.lower() if item.custom_lot_no else ""
            batch_lot_no = batch.custom_lot_no.lower() if batch.custom_lot_no else ""
            if doc_lot_no != batch_lot_no:
                frappe.throw(
                    f"Lot No is not the same as the lot no in Batch {batch.name}"
                )

            doc_container_no = (
                item.custom_container_no.lower() if item.custom_container_no else ""
            )
            batch_container_no = (
                batch.custom_container_no.lower() if batch.custom_container_no else ""
            )
            if doc_container_no != batch_container_no:
                frappe.throw(
                    f"Container no is not the same as the container no in Batch {batch.name}"
                )

        # set_total_cone(doc)

        # Uncomment and add similar case-insensitive checks if needed for these fields
        # doc_supplier_batch_no = item.custom_supplier_batch_no.lower() if item.custom_supplier_batch_no else ""
        # batch_supplier_batch_no = batch.custom_supplier_batch_no.lower() if batch.custom_supplier_batch_no else ""
        # if doc_supplier_batch_no != batch_supplier_batch_no:
        #     frappe.throw(f'Supplier Batch No is not the same as the supplier batch no in Batch {batch.name}')

        # doc_cone = item.custom_cone.lower() if item.custom_cone else ""
        # batch_cone = batch.custom_cone.lower() if batch.custom_cone else ""
        # if doc_cone != batch_cone:
        #     frappe.throw(f'Cone is not the same as the cone in Batch {batch.name}')


@frappe.whitelist()
def get_delivery_note_batch(
    lot_no=None,
    container_no=None,
    supplier_batch_no=None,
    glue=None,
    pulp=None,
    fsc=None,
    lusture=None,
    grade=None,
    cone=None,
    denier=None,
):

    filters = {}

    # Add filters based on available parameters
    if lot_no:
        filters["custom_lot_no"] = lot_no
    if container_no:
        filters["custom_container_no"] = container_no
    if supplier_batch_no:
        filters["custom_supplier_batch_no"] = supplier_batch_no
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
    if cone:
        filters["custom_cone"] = cone
    if denier:
        filters["item_name"] = denier

    # Check if at least one filter is applied
    if filters:
        if frappe.db.exists("Batch", filters):
            item = frappe.get_doc("Batch", filters)

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
def get_item_batch(batch):
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
def update_item_batch(doc, method=None):
    for item in doc.items:
        remaining_cone = cint(
            frappe.db.get_value("Batch", item.batch_no, "custom_cone")
        ) - cint(item.custom_cone)
        frappe.db.set_value("Batch", item.batch_no, "custom_cone", remaining_cone)
        frappe.db.commit()


@frappe.whitelist()
def get_batches(container_no, lot_no):
    # frappe.msgprint("container_no: {0} lot_no: {1}".format(container_no, lot_no))
    batches = frappe.get_all(
        "Batch",
        filters={"custom_container_no": container_no, "custom_lot_no": lot_no},
        fields=[
            "name",
            "item",
            "item_name",
            "batch_qty",
            "stock_uom",
            "custom_supplier_batch_no",
            "custom_cone",
            "custom_lusture",
            "custom_grade",
            "custom_glue",
            "custom_pulp",
            "custom_fsc",
        ],
    )
    return batches


@frappe.whitelist()
def get_lot_nos(container_no):
    lot_nos = frappe.get_all(
        "Batch", filters={"custom_container_no": container_no}, fields=["custom_lot_no"]
    )
    return lot_nos[0].get("custom_lot_no") if lot_nos else None


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
        existing_item = next(
            (item for item in items if item["item"] == batch.item), None
        )
        if existing_item:
            # If the item exists, increase the batch_qty
            existing_item["batch_qty"] += float(batch.qty)
        else:
            # If the item does not exist, add it to the list
            items.append(
                {
                    "item": batch.item,
                    "batch_qty": float(batch.qty),
                    "stock_uom": batch.uom,
                    "name": batch.batch_id,
                }
            )
    return items


@frappe.whitelist()
def get_item_batches(item_code):
    items = get_items()
    batches = []
    doc = frappe.get_last_doc("Container")
    for item in items:
        for batch in doc.batches:
            if item["item"] == item_code:
                batches.append(
                    {
                        "batch_id": batch.batch_id,
                        "qty": float(batch.qty),
                        "uom": batch.uom,
                        "cone": batch.cone,
                        "supplier_batch_no": batch.supplier_batch_no,
                    }
                )
    return batches


@frappe.whitelist()
def create_serial_and_batch_bundle(item_code):
    try:
        batches = get_item_batches(item_code)
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
            sb_bundle.append(
                "entries",
                {
                    "batch_no": batch["batch_id"],
                    "qty": batch["qty"],
                    "uom": batch["uom"],
                    "cone": batch["cone"],
                    "supplier_batch_no": batch["supplier_batch_no"],
                    "warehouse": "Finished Goods - MC",
                },
            )

        sb_bundle.save(ignore_permissions=True)
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
        purchase_receipt.append(
            "items",
            {
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
                "serial_and_batch_bundle": serial_and_batch_bundle,
            },
        )

    # Save and submit the Purchase Receipt
    try:
        purchase_receipt.save(ignore_permissions=True)
        purchase_receipt.submit()
        frappe.db.commit()

        return {
            "message": "Purchase Receipt created successfully",
            "purchase_receipt": purchase_receipt.name,
        }
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


@frappe.whitelist()
def delete_batches():
    try:
        frappe.db.sql("DELETE FROM `tabContainer`")
        frappe.db.commit()
        return "Batches deleted successfully"
    except Exception as e:
        frappe.throw(f"Error deleting Batches: {e}")


@frappe.whitelist()
def update_batch_stock():
    # Fetch the last 10  batches with their quantities
    batches = frappe.get_all("Update Batch", fields=["batch_id", "batch_quantity"])
    data = []

    for batch in batches:
        if frappe.db.exists("Batch", batch.get("batch_id")):
            frappe.db.set_value(
                "Batch", batch.get("batch_id"), "batch_qty", batch.get("batch_quantity")
            )
            frappe.db.commit()
    return "Batch stock updated successfully"


@frappe.whitelist()
def delete_docs():
    try:
        frappe.db.sql("DELETE FROM `tabUpdate Batch`")
        frappe.db.commit()
        return "Documents deleted successfully"
    except Exception as e:
        frappe.throw(f"Error deleting Documents: {e}")


@frappe.whitelist()
def print_selected_docs(doctype, docnames):
    import json
    from frappe.utils.print_format import download_multi_pdf

    docnames = json.loads(docnames)
    doctype_dict = {doctype: docnames}

    pdf_data = download_multi_pdf(
        doctype_dict,
        doctype,
        "Batch",
        no_letterhead=False,
        letterhead=None,
        options=None,
    )
    return pdf_data


@frappe.whitelist()
def generate_multi_pdf_url(batches, doc_name):
    name = "Batch"
    # batches = []
    # for b in self.list_batches:
    #     batches.append(b.batch)

    doctype = {"Batch": batches}

    try:
        format = "Batch"
        download_multi_pdf(doctype, name, format)
        pdf_content = frappe.local.response.filecontent

        if not pdf_content:
            raise ValueError("PDF content is empty or not generated correctly.")

        # Construct the filename
        name_str = name.replace(" ", "-").replace("/", "-")
        filename = f"combined_{name_str}.pdf"

        # Save the PDF content as a File document in the database
        _file = frappe.get_doc(
            {
                "doctype": "File",
                "file_name": filename,
                "is_private": 0,
                "content": pdf_content,
            }
        )
        _file.save()
        frappe.db.commit()
        file_url = _file.file_url
        frappe.db.set_value("Print Batch", doc_name, "file_url", file_url)
        # reload the form
        frappe.msgprint(
            f"PDF generated successfully. <a href='{file_url}' target='_blank'>Click here</a> to print the PDF."
        )

    except Exception as e:
        frappe.log_error(f"Error generating PDF URL: {str(e)}")
        frappe.throw(f"Failed to generate PDF: {str(e)}")


@frappe.whitelist()
def get_print_batch(lot_no, container_no, supplier_batch_no):

    if frappe.db.exists(
        "Batch",
        {
            "custom_supplier_batch_no": supplier_batch_no,
            "custom_lot_no": lot_no,
            "custom_container_no": container_no,
        },
    ):
        batch = frappe.get_doc(
            "Batch",
            {
                "custom_supplier_batch_no": supplier_batch_no,
                "custom_lot_no": lot_no,
                "custom_container_no": container_no,
            },
        )
        data = {
            "item": batch.item,
            "batch": batch.name,
            "cone": batch.custom_cone,
            "lot_no": batch.custom_lot_no,
            "batch_qty": batch.batch_qty,
        }
        return data


@frappe.whitelist()
def update_container():
    frappe.db.sql(
        """
        UPDATE `tabContainer` 
        SET total_net_weight = (
            SELECT SUM(qty) 
            FROM `tabBatch Items` 
            WHERE parent = tabContainer.name
        )
    """
    )
    frappe.db.commit()
    return "Container updated successfully"


@frappe.whitelist()
def update_container_batch_qty():
    message = []
    containers = frappe.get_all("Container", fields=["name"])
    for container in containers:
        container_doc = frappe.get_doc("Container", container.name)
        for batch in container_doc.batches:
            frappe.db.set_value("Batch", batch.batch_id, "batch_qty", batch.qty)
            frappe.db.commit()
        message.append(f"Container {container.name} updated successfully")
    return message


@frappe.whitelist()
def resend_email_queue():
    from frappe.email.doctype.email_queue.email_queue import send_now

    emails = frappe.get_all("Email Queue", {"status": "Not Sent"})
    for email in emails:
        send_now(email.name)
    return "Email Queue updated successfully"


@frappe.whitelist()
def update_custom_item_length():
    # update th custom_item_length in the delivery note
    notes = frappe.get_all("Delivery Note", fields=["name"])
    for note in notes:
        doc = frappe.get_doc("Delivery Note", note.name)
        frappe.db.set_value(
            "Delivery Note", note.name, "custom_item_length", len(doc.items)
        )
        frappe.db.commit()


@frappe.whitelist()
def create_batches(container):
    frappe.publish_realtime(
        "site_creation", {"message": "Creating Batches"}, user=frappe.session.user
    )
    container_doc = frappe.get_doc("Container", container)

    for batch in container_doc.batches:
        if frappe.db.exists("Batch", batch.batch_id):
            continue
        else:
            batch_doc = frappe.new_doc("Batch")
            batch_doc.item = batch.item
            batch_doc.batch_qty = batch.qty
            batch_doc.stock_uom = batch.uom
            batch_doc.batch_id = batch.batch_id
            batch_doc.custom_supplier_batch_no = batch.supplier_batch_no
            batch_doc.custom_container_no = container_doc.container_no
            batch_doc.custom_cone = batch.cone
            batch_doc.custom_glue = container_doc.glue
            batch_doc.custom_lusture = container_doc.lusture
            batch_doc.custom_grade = container_doc.grade
            batch_doc.custom_pulp = container_doc.pulp
            batch_doc.custom_fsc = container_doc.fsc
            batch_doc.custom_lot_no = container_doc.lot_no
            batch_doc.save(ignore_permissions=True)
            batch_doc.submit()
    create_purchase_receipt(container_doc.name)
    frappe.db.commit()


def create_purchase_receipt(container):
    # Fetch the Container document using the passed container ID
    container_doc = frappe.get_doc("Container", container)

    items = container_doc.get_items()

    # Create a new Purchase Receipt document
    purchase_receipt = frappe.new_doc("Purchase Receipt")
    purchase_receipt.supplier = container_doc.supplier
    purchase_receipt.posting_date = container_doc.posting_date
    purchase_receipt.custom_container_no = container_doc.name
    purchase_receipt.custom_total_batches = len(container_doc.batches)
    purchase_receipt.items = []

    # Add items to the Purchase Receipt
    for item in items:
        serial_and_batch_bundle = container_doc.create_serial_and_batch_bundle(
            item["item"]
        )
        purchase_receipt.append(
            "items",
            {
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
                "serial_and_batch_bundle": serial_and_batch_bundle,
            },
        )

    # Save and submit the Purchase Receipt
    try:
        purchase_receipt.save()
        purchase_receipt.submit()
        frappe.db.commit()
        return purchase_receipt.name
    except Exception as e:
        frappe.db.rollback()
        frappe.log_error(frappe.get_traceback(), "create_purchase_receipt")
        frappe.msgprint(
            {"message": "Failed to create Purchase Receipt", "error": str(e)}
        )


@frappe.whitelist()
def update_pr_with_container_details():
    frappe.db.sql(
        """
        UPDATE `tabPurchase Receipt` AS pr
        JOIN `tabContainer` AS c ON pr.custom_container_no = c.name
        SET 
            pr.custom_lot_number = c.lot_no,
            pr.custom_lusture = c.lusture,
            pr.custom_glue = c.glue,
            pr.custom_grade = c.grade,
            pr.custom_pulp = c.pulp,
            pr.custom_fsc = c.fsc,
            pr.custom_merge_no = c.merge_no
    """
    )
    frappe.db.commit()


update_pr_with_container_details()


@frappe.whitelist()
def delete_containers(doctype):
    frappe.db.sql(f"DELETE FROM `tab{doctype}`")
    frappe.db.commit()
    return "Success"


def update_batch_qty():
    container = frappe.get_all("Container", {"docstatus": 1}, ["name"])
    for container in container:
        container_doc = frappe.get_doc("Container", container.name)
        for batch in container_doc.batches:
            # update batch if batch id exists and container no is the same adn lot no is the same
            frappe.db.sql(
                f"UPDATE `tabBatch` SET batch_qty = {batch.qty} WHERE name = '{batch.batch_id}' AND custom_container_no = '{container_doc.container_no}' AND custom_lot_no = '{container_doc.lot_no}'"
            )
            frappe.db.commit()
    return "Success"


@frappe.whitelist()
def enqueue_update_batch_qty():
    frappe.enqueue("mhr.utilis.update_batch_qty", queue="long")
    return "Success"


@frappe.whitelist()
def update_container_batch_qty(container: str):
    container_doc = frappe.get_doc("Container", container)
    for batch in container_doc.batches:
        frappe.db.sql(
            f"UPDATE `tabBatch` SET batch_qty = {batch.qty} WHERE name = '{batch.batch_id}'"
        )
        frappe.db.commit()
    return "Success"


@frappe.whitelist()
def set_delivery_note_user(doc, method=None):
    doc.prepared_by = frappe.session.user


@frappe.whitelist()
def rename_delivery_note():
    delivery_notes = frappe.get_all("Delivery Trip", ["name", "challan_number"])

    # Find the highest existing challan number
    max_challan = 0
    for dn in delivery_notes:
        if dn.challan_number:
            try:
                challan_int = int(dn.challan_number)
                max_challan = max(max_challan, challan_int)
            except (ValueError, TypeError):
                continue

    for delivery_note in delivery_notes:
        challan_no = delivery_note.challan_number

        try:
            if not challan_no:
                # For empty challan, use next available number after max_challan
                max_challan += 1
                new_name = str(max_challan)

                # Keep incrementing until we find an unused number
                while frappe.db.exists("Delivery Trip", new_name) or frappe.db.exists(
                    "Delivery Trip", {"challan_number": new_name}
                ):
                    max_challan += 1
                    new_name = str(max_challan)
            else:
                # Use existing challan number logic
                new_name = challan_no
                counter = int(challan_no)

                while frappe.db.exists("Delivery Trip", new_name):
                    counter += 1
                    new_name = str(counter)

                    while frappe.db.exists(
                        "Delivery Trip", {"challan_number": new_name}
                    ):
                        counter += 1
                        new_name = str(counter)

            # Update both the document name and challan_number
            frappe.db.sql(
                """
                UPDATE `tabDelivery Trip` 
                SET name = %s, challan_number = %s 
                WHERE name = %s
            """,
                (new_name, new_name, delivery_note.name),
            )
            frappe.db.commit()
        except (ValueError, TypeError):
            continue

    return "Success"


@frappe.whitelist()
def autoname(doc, method=None):
    doc.name = doc.challan_number


@frappe.whitelist()
def check_batch_already_used_in_delivery_note(batch_no):
    """
    Checks if a batch is already used in any Delivery Note Item

    Args:
        batch_no (str): The batch number to check

    Returns:
        dict: A dictionary with 'used' status and delivery note name if used
    """
    if not batch_no:
        return {"used": False}

    # Check if the batch exists in any Delivery Note Item that is part of a submitted Delivery Note
    delivery_note = frappe.db.sql(
        """
        SELECT batch_no 
        FROM `tabDelivery Note Item` 
        WHERE batch_no = %s 
        LIMIT 1
    """,
        batch_no,
        as_dict=True,
    )

    if delivery_note:
        return {"used": True, "delivery_note": delivery_note[0].batch_no}

    return {"used": False}


@frappe.whitelist()
def validate_delivery_note_batches(doc, method=None):
    """
    Validates that none of the batches in a Delivery Note are already used in other submitted Delivery Notes

    Args:
        doc: The Delivery Note document
        method: The trigger method (validate, before_save, etc.)
    """

    for item in doc.items:
        if item.batch_no:
            # Check if this batch is used in any other Delivery Note Item
            exists = frappe.db.sql(
                """
                SELECT name FROM `tabDelivery Note Item`
                WHERE batch_no = %s AND parent != %s
                LIMIT 1
            """,
                (item.batch_no, doc.name),
                as_dict=1,
            )

            if exists:
                frappe.throw(
                    _(
                        "Batch {0} is already used. Please select a different batch."
                    ).format(item.batch_no)
                )


@frappe.whitelist()
def get_number_of_boxes(container_name):
    # for the number of boxes select only the batches that have the same cone and contianer nad have batch_qty more than 0 from batch doctype
    # return frappe.db.count(
    #     "Batch",
    #     {
    #         "custom_container_no": container_name,
    #         "batch_qty": (">", 0),
    #     },
    # )
    query = """
        SELECT COUNT(*) as count
        FROM `tabBatch` b
        WHERE b.custom_container_no = %s AND b.batch_qty > 0
    """
    result = frappe.db.sql(query, (container_name,), as_dict=1)
    return result[0].count if result else 0


@frappe.whitelist()
def update_container_item():
    frappe.db.sql("""
    UPDATE `tabBatch Items` cb
    JOIN `tabContainer` c ON cb.parent = c.name
    SET cb.item = c.item
    WHERE cb.parenttype = 'Container' AND cb.parentfield = 'batches'
    """)
    frappe.db.commit()
    return "successfully update batches"

@frappe.whitelist()
def submit_docs(doctype):
    docs = frappe.get_all(doctype, {"docstatus": 0})
    for doc in docs:
        d = frappe.get_doc(doctype, doc.name)
        d.submit()
        frappe.db.commit()
    return "docs submitted successfully"