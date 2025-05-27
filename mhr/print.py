import frappe
from frappe.utils.print_format import download_pdf

@frappe.whitelist(allow_guest=True)
def preview(name):
    frappe.set_user("Administrator")
    return download_pdf(doctype="Delivery Note", name=name, format="Delivery Note")
