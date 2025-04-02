import frappe
from frappe import _
from frappe.utils import cint, flt


def execute(filters=None):
    columns, data = get_columns(), get_datas(filters=filters)
    return columns, data


def get_columns():
    return [
        {"label": _("Date"), "fieldname": "date", "fieldtype": "Date", "width": 150},
        {
            "label": _("Container"),
            "fieldname": "container",
            "fieldtype": "Data",
            "width": 150,
        },
        {
            "label": _("Product Name"),
            "fieldname": "item",
            "fieldtype": "Data",
            "width": 100,
        },
        {"label": _("Pulp"), "fieldname": "pulp", "fieldtype": "Data", "width": 100},
        {
            "label": _("Lusture"),
            "fieldname": "lusture",
            "fieldtype": "Data",
            "width": 100,
        },
        {"label": _("Glue"), "fieldname": "glue", "fieldtype": "Data", "width": 100},
        {
            "label": _("Total Closing"),
            "fieldname": "total_closing",
            "fieldtype": "Data",
            "width": 100,
        },
        {"label": _("Grade"), "fieldname": "grade", "fieldtype": "Data", "width": 100},
        {
            "label": _("Mer No"),
            "fieldname": "mer_no",
            "fieldtype": "Data",
            "width": 100,
        },
        {
            "label": _("Lot No"),
            "fieldname": "lot_no",
            "fieldtype": "Data",
            "width": 100,
        },
        {"label": _("Cone"), "fieldname": "cone", "fieldtype": "Data", "width": 100},
        {"label": _("Boxes"), "fieldname": "boxes", "fieldtype": "Data", "width": 100},
        {"label": _("Stock"), "fieldname": "stock", "fieldtype": "Data", "width": 100},
        {
            "label": _("Warehouse"),
            "fieldname": "warehouse",
            "fieldtype": "Data",
            "width": 100,
        },
        {
            "label": _("Cross Section"),
            "fieldname": "cross_section",
            "fieldtype": "Data",
            "width": 100,
        },
    ]


def get_datas(filters=None):
    conditions = ""
    if filters:
        if filters.get("from_date") and filters.get("to_date"):
            conditions += " AND c.posting_date BETWEEN %(from_date)s AND %(to_date)s"
        elif filters.get("from_date"):
            conditions += " AND c.posting_date >= %(from_date)s"
        elif filters.get("to_date"):
            conditions += " AND c.posting_date <= %(to_date)s"
    else:
        # Default to last 30 days if no filters
        conditions += " AND c.posting_date >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)"

    query = """
        SELECT DISTINCT
            c.posting_date as date,
            c.container_no as container,
            c.item,
            c.pulp,
            c.lusture,
            c.glue,
            c.grade,
            c.merge_no as mer_no,
            c.lot_no,
            c.warehouse,
            c.cross_section,
            c.name as container_name
        FROM 
            `tabContainer` c
        WHERE 
            c.docstatus = 1
            {conditions}
        ORDER BY 
            c.posting_date DESC, c.container_no
    """.format(
        conditions=conditions
    )

    containers = frappe.db.sql(query, filters, as_dict=1)
    data = []

    for container in containers:
        cones = get_multiple_variable_of_cone(container.container_name)
        for cone in cones:
            row = container.copy()
            row.update(
                {
                    "total_closing": get_total_closing(container.container_name),
                    "cone": cone,
                    "boxes": get_number_of_boes(container.container_name, cone),
                    "stock": get_cone_total(container.container_name, cone),
                }
            )
            data.append(row)

    return data


def get_multiple_variable_of_cone(container_name):
    query = """
        SELECT DISTINCT cone 
        FROM `tabBatch Items` 
        WHERE parent = %s
    """
    cones = frappe.db.sql(query, container_name, as_dict=1)
    return [d.cone for d in cones]


def get_cone_total(container_name, cone):
    query = """
        SELECT SUM(b.batch_qty) as total
        FROM `tabBatch Items` cb
        LEFT JOIN `tabBatch` b ON b.name = cb.batch_id
        WHERE cb.parent = %s AND cb.cone = %s
    """
    result = frappe.db.sql(query, (container_name, cone), as_dict=1)
    return flt(result[0].total) if result and result[0].total else 0


def get_total_closing(container_name):
    query = """
        SELECT SUM(b.batch_qty) as total
        FROM `tabBatch Items` cb
        LEFT JOIN `tabBatch` b ON b.name = cb.batch_id
        WHERE cb.parent = %s
    """
    result = frappe.db.sql(query, container_name, as_dict=1)
    return flt(result[0].total) if result and result[0].total else 0


def get_number_of_boes(container_name, cone):
    query = """
        SELECT COUNT(*) as count
        FROM `tabBatch Items`
        WHERE parent = %s AND cone = %s
    """
    result = frappe.db.sql(query, (container_name, cone), as_dict=1)
    return result[0].count if result else 0
