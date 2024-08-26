import frappe
from frappe import _
from frappe.utils import cint

def execute(filters=None):
    columns, data = get_columns(filters=filters), get_datas(filters=filters)
    return columns, data

def get_columns(filters=None):
    return [
        {"label": _("Date"), "fieldname": "date", "fieldtype": "Date", "width": 150},
        {"label": _("Container"), "fieldname": "container", "fieldtype": "Data", "width": 150},
        {"label": _("Product Name"), "fieldname": "item", "fieldtype": "Data", "width": 100},
        {"label": _("Pulp"), "fieldname": "pulp", "fieldtype": "Data", "width": 100},
        {"label": _("Lusture"), "fieldname": "lusture", "fieldtype": "Data", "width": 100},
        {"label": _("Glue"), "fieldname": "glue", "fieldtype": "Data", "width": 100},
        {"label": _("Total Closing"), "fieldname": "total_closing", "fieldtype": "Data", "width": 100},
        {"label": _("Mer No"), "fieldname": "mer_no", "fieldtype": "Data", "width": 100},
        {"label": _("Lot No"), "fieldname": "lot_no", "fieldtype": "Data", "width": 100},
        {"label": _("Cone"), "fieldname": "cone", "fieldtype": "Data", "width": 100},
        {"label": _("Stock"), "fieldname": "stock", "fieldtype": "Data", "width": 100},
    ]

def get_datas(filters=None):
    containers = frappe.get_all("Container", fields=["*"], filters=filters)
    data = []

    for container in containers:
        cones = get_multiple_variable_of_cone(container.name)
        for cone in cones:
            data.append({
                "date": container.posting_date if cone == cones[0] else "",
                "container": container.container_no if cone == cones[0] else "",
                "item": container.item if cone == cones[0] else "",
                "pulp": container.pulp if cone == cones[0] else "",
                "lusture": container.lusture if cone == cones[0] else "",
                "glue": container.glue if cone == cones[0] else "",
                "total_closing": get_total_closing(container.name) if cone == cones[0] else "",
                "mer_no": container.merge_no if cone == cones[0] else "",
                "lot_no": container.lot_no if cone == cones[0] else "",
                "cone": cone,
                "stock": get_cone_total(container.name, cone)
            })
        
    return data

def get_multiple_variable_of_cone(container_name):
    con = frappe.get_doc("Container", container_name)
    return list(set(batch.cone for batch in con.batches))

def get_cone_total(container_name, cone):
    con = frappe.get_doc("Container", container_name)
    return sum(cint(batch.cone) for batch in con.batches if batch.cone == cone)

def get_total_closing(container_name):
    con = frappe.get_doc("Container", container_name)
    return sum(cint(batch.qty) for batch in con.batches)
