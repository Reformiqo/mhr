# MI1-I50 (2026-06-23 follow-up): seed the "Job Work Received" Stock
# Entry Type so make_receive_from_subcontractor can use it. Purpose is
# "Material Transfer" so both source + target warehouses stay populated
# on the resulting draft (Raj wants the warehouses preserved exactly
# as they were on the Send entry — see screenshots on MI1-I50).
#
# Idempotent — safe to re-run.

import frappe


def execute():
    if frappe.db.exists("Stock Entry Type", "Job Work Received"):
        return
    doc = frappe.new_doc("Stock Entry Type")
    doc.name = "Job Work Received"
    doc.purpose = "Material Transfer"
    doc.insert(ignore_permissions=True)
    frappe.db.commit()
