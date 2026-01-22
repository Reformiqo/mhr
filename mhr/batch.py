import frappe

@frappe.whitelist()
def recalculate_batch_qty():
    docs = frappe.get_all("Batch", ["name"])
    for doc in docs:
        d = frappe.get_doc("Batch", doc.name)
        d.recalculate_batch_qty()

@frappe.whitelist()
def enqueue_recalculate_batch_qty():
    frappe.enqueue(recalculate_batch_qty, queue="long")