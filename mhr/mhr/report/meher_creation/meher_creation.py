import frappe
from frappe import _
from frappe.utils import flt


def execute(filters=None):
    columns = get_columns()
    data = get_datas(filters=filters)
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
        {"label": _("Stock"), "fieldname": "stock", "fieldtype": "Float", "width": 100},
        {
            "label": _("Warehouse"),
            "fieldname": "warehouse",
            "fieldtype": "Data",
            "width": 100,
        },
        {
            "label": _("Supplier Batch No"),
            "fieldname": "supplier_batch_no",
            "fieldtype": "Data",
            "width": 100,
        },
        {
            "label": _("Batch"),
            "fieldname": "batch",
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
    try:
        # Apply pagination - get data in chunks to prevent timeouts
        page_size = 100
        page = 1
        all_data = []
        
        # Build conditions for filtering
        conditions = ""
        if filters:
            if filters.get("from_date") and filters.get("to_date"):
                conditions += " AND c.posting_date BETWEEN %(from_date)s AND %(to_date)s"
            
        else:
            # Default to last 30 days if no filters
            conditions += " AND c.posting_date >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)"
        
        # First, get all container names and container numbers in one query
        container_query = """
            SELECT 
                c.name as container_name,
                c.container_no
            FROM 
                `tabContainer` c
            WHERE 
                c.docstatus = 1
                {conditions}
            ORDER BY 
                c.posting_date DESC, c.container_no
        """.format(conditions=conditions)
        
        all_containers = frappe.db.sql(container_query, filters, as_dict=1)
        
        # Get all supplier batch numbers in one query
        container_nos = [c.container_no for c in all_containers]
        supplier_batch_dict = {}
        
        if container_nos:
            supplier_batch_query = """
                SELECT 
                    custom_container_no, 
                    custom_supplier_batch_no 
                FROM 
                    `tabBatch` 
                WHERE 
                    custom_container_no IN %s
            """
            supplier_batches = frappe.db.sql(
                supplier_batch_query, [container_nos], as_dict=1
            )
            
            for batch in supplier_batches:
                supplier_batch_dict[batch.custom_container_no] = batch.custom_supplier_batch_no
        
        # Process containers in batches
        for i in range(0, len(all_containers), page_size):
            batch_containers = all_containers[i:i+page_size]
            container_names = [c.container_name for c in batch_containers]
            
            if not container_names:
                continue
                
            # Get container details in one query
            container_details_query = """
                SELECT
                    c.name as container_name,
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
                    c.cross_section
                FROM 
                    `tabContainer` c
                WHERE 
                    c.name IN %s
                ORDER BY 
                    c.posting_date DESC, c.container_no
            """
            
            containers = frappe.db.sql(
                container_details_query, [container_names], as_dict=1
            )
            
            # Get all cones data in one query
            cones_query = """
                SELECT 
                    parent, 
                    cone
                FROM 
                    `tabBatch Items`
                WHERE 
                    parent IN %s
                GROUP BY 
                    parent, cone
            """
            
            cones_data = frappe.db.sql(cones_query, [container_names], as_dict=1)
            
            # Create a dictionary of cones by container
            cones_by_container = {}
            for cone_item in cones_data:
                if cone_item.parent not in cones_by_container:
                    cones_by_container[cone_item.parent] = []
                cones_by_container[cone_item.parent].append(cone_item.cone)
            
            # Get all batch quantities in one query
            batch_data_query = """
                SELECT 
                    cb.parent,
                    cb.cone,
                    SUM(b.batch_qty) as total_qty,
                    COUNT(CASE WHEN b.batch_qty > 0 THEN 1 END) as box_count
                FROM 
                    `tabBatch Items` cb
                LEFT JOIN 
                    `tabBatch` b ON b.name = cb.batch_id
                WHERE 
                    cb.parent IN %s
                GROUP BY 
                    cb.parent, cb.cone
            """
            
            batch_data = frappe.db.sql(batch_data_query, [container_names], as_dict=1)
            
            # Create dictionaries for easy lookup
            qty_by_container_cone = {}
            boxes_by_container_cone = {}
            
            for item in batch_data:
                key = f"{item.parent}_{item.cone}"
                qty_by_container_cone[key] = flt(item.total_qty)
                boxes_by_container_cone[key] = item.box_count
            
            # Get total closing quantities in one query
            total_closing_query = """
                SELECT 
                    cb.parent,
                    SUM(b.batch_qty) as total
                FROM 
                    `tabBatch Items` cb
                LEFT JOIN 
                    `tabBatch` b ON b.name = cb.batch_id
                WHERE 
                    cb.parent IN %s
                GROUP BY 
                    cb.parent
            """
            
            total_closing_data = frappe.db.sql(
                total_closing_query, [container_names], as_dict=1
            )
            
            # Create dictionary for total closing
            total_closing_by_container = {}
            for item in total_closing_data:
                total_closing_by_container[item.parent] = flt(item.total)
            
            # Build the final data structure
            for container in containers:
                container_name = container.container_name
                supplier_batch_no = supplier_batch_dict.get(container.container, "")
                
                # Get all cones for this container
                cones = cones_by_container.get(container_name, [])
                
                # Total closing for this container
                total_closing = total_closing_by_container.get(container_name, 0)
                
                for cone in cones:
                    row = container.copy()
                    key = f"{container_name}_{cone}"
                    
                    row.update({
                        "total_closing": total_closing,
                        "cone": cone,
                        "boxes": boxes_by_container_cone.get(key, 0),
                        "stock": qty_by_container_cone.get(key, 0),
                        "supplier_batch_no": supplier_batch_no
                    })
                    
                    all_data.append(row)
            
            page += 1
        
        return all_data
        
    except Exception as e:
        frappe.log_error(f"Error in Meher Creation Report: {str(e)}", "Meher Creation Report Error")
        return []
