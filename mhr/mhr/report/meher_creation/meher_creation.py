import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, add_days, today
from erpnext.accounts.utils import get_fiscal_year

# Report Configuration
report_config = {"is_background_report": False, "refresh_timeout": 30}  # seconds


def execute(filters=None):
   

    columns, data = get_columns(), get_datas(filters=filters)
    return columns, data


def validate_filters(filters):
    from_date = getdate(filters.get("from_date"))
    to_date = getdate(filters.get("to_date"))

    if from_date > to_date:
        frappe.throw(_("From Date cannot be greater than To Date"))

    # Validate if date range is not more than 31 days
    date_diff = frappe.utils.date_diff(to_date, from_date)
    if date_diff > 31:
        frappe.throw(_("Date range cannot be more than 31 days"))

    # Validate fiscal year for dates
    try:
        get_fiscal_year(from_date, verbose=0)
        get_fiscal_year(to_date, verbose=0)
    except Exception:
        frappe.msgprint(
            _(
                "Some of the dates selected are not within any active Fiscal Year. The report may not include all data."
            ),
            indicator="yellow",
            alert=True,
        )


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
            "fieldtype": "Float",
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
        {"label": _("Boxes"), "fieldname": "boxes", "fieldtype": "Int", "width": 100},
        {"label": _("Stock"), "fieldname": "stock", "fieldtype": "Int", "width": 100},
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
    conditions = get_conditions(filters)

    query = """
        SELECT 
            c.posting_date as date,
            c.container_no as container,
            c.item,
            c.pulp,
            c.lusture,
            c.glue,
            SUM(cb.qty) as total_closing,
            c.grade,
            c.merge_no as mer_no,
            c.lot_no,
            cb.cone,
            COUNT(cb.name) as boxes,
            SUM(cb.cone) as stock,
            c.warehouse,
            c.cross_section
        FROM 
            `tabContainer` c
        LEFT JOIN 
            `tabContainer Batch` cb ON c.name = cb.parent
        WHERE 
            c.docstatus = 1 
        GROUP BY 
            c.name, cb.cone
        ORDER BY 
            c.posting_date DESC, c.container_no, cb.cone
    """

    return frappe.db.sql(query, filters, as_dict=1)


def get_conditions(filters):
    conditions = []

    if filters.get("from_date"):
        conditions.append("DATE(c.posting_date) >= %(from_date)s")
    if filters.get("to_date"):
        conditions.append("DATE(c.posting_date) <= %(to_date)s")

    return " AND " + " AND ".join(conditions) if conditions else ""
