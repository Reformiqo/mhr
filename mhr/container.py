import frappe

@frappe.whitelist()
def get_container(container):
    return frappe.get_doc("Container", container)