import frappe
from frappe import _
from frappe.utils import cint


def execute(filters=None):
    columns, data = get_columns(filters), get_data(filters)
    return columns, data


def get_columns(filters=None):
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
            "label": _("Grade"),
            "fieldname": "grade",
            "fieldtype": "Data",
            "width": 100,
        },
        {
            "label": _("Total Closing"),
            "fieldname": "total_closing",
            "fieldtype": "Int",
            "width": 100,
        },
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
        {"label": _("Boxes"), "fieldname": "boxes", "fieldtype": "Int", "width": 100},
        {"label": _("Stock"), "fieldname": "stock", "fieldtype": "Int", "width": 100},
    ]


def get_data(filters=None):
    conditions = get_conditions(filters)

    query = """
        SELECT 
            c.posting_date as date,
            c.container_no as container,
            c.item,
            c.pulp,
            c.lusture,
            c.glue,
            c.grade,
            c.merge_no as mer_no,
            c.lot_no,
            cb.cone,
            COUNT(cb.name) as boxes,
            SUM(cb.qty) as total_closing,
            SUM(CASE WHEN cb.cone = cb.cone THEN cb.cone ELSE 0 END) as stock
        FROM 
            `tabContainer` c
        LEFT JOIN 
            `tabContainer Batch` cb ON c.name = cb.parent
        WHERE 
            1=1
            {conditions}
        GROUP BY 
            c.name, cb.cone
        ORDER BY 
            c.posting_date DESC, c.container_no, cb.cone
    """.format(
        conditions=conditions
    )

    return frappe.db.sql(query, filters, as_dict=1)


def get_conditions(filters):
    conditions = []

    if filters:
        if filters.get("posting_date"):
            conditions.append("c.posting_date = %(posting_date)s")
        if filters.get("container_no"):
            conditions.append("c.container_no = %(container_no)s")
        if filters.get("item"):
            conditions.append("c.item = %(item)s")
        if filters.get("merge_no"):
            conditions.append("c.merge_no = %(merge_no)s")
        if filters.get("lot_no"):
            conditions.append("c.lot_no = %(lot_no)s")
        if filters.get("grade"):
            conditions.append("c.grade = %(grade)s")

    return " AND ".join(conditions) if conditions else ""
