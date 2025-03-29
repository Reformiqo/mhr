import frappe
from frappe import _
from frappe.utils import cint, flt

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
        {"label": _("Total Closing"), "fieldname": "total_closing", "fieldtype": "Float", "width": 100},
        {"label": _("Grade"), "fieldname": "grade", "fieldtype": "Data", "width": 100},
        {"label": _("Mer No"), "fieldname": "mer_no", "fieldtype": "Data", "width": 100},
        {"label": _("Lot No"), "fieldname": "lot_no", "fieldtype": "Data", "width": 100},
        {"label": _("Cone"), "fieldname": "cone", "fieldtype": "Data", "width": 100},
        {"label": _("Boxes"), "fieldname": "boxes", "fieldtype": "Data", "width": 100},
        {"label": _("Stock"), "fieldname": "stock", "fieldtype": "Data", "width": 100},
        {"label": _("Warehouse"), "fieldname": "warehouse", "fieldtype": "Data", "width": 100},
        {"label": _("Cross Section"), "fieldname": "cross_section", "fieldtype": "Data", "width": 100},
        
    ]

def get_datas(filters=None):
    containers = frappe.get_all("Container", fields=["*"], filters=filters)
    data = []

    for container in containers:
        cones = get_multiple_variable_of_cone(container.name)
        for cone in cones:
            data.append({
                "date": container.posting_date,
                "container": container.container_no,
                "item": container.item,
                "pulp": container.pulp,
                "lusture": container.lusture,
                "glue": container.glue,
                "total_closing": get_total_closing(container.name),
                "mer_no": container.merge_no,
                "lot_no": container.lot_no,
                "grade": container.grade,
                "cone": cone,
                "boxes": get_number_of_boes(container.name, cone),
                "stock": get_cone_total(container.name, cone),
                "warehouse": container.warehouse,
                "cross_section": container.cross_section,
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
    return sum(flt(batch.qty) for batch in con.batches)

def get_number_of_boes(container_name, cone):
    con = frappe.get_doc("Container", container_name)
    return len([batch for batch in con.batches if batch.cone == cone])