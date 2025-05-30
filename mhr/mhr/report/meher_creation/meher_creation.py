import frappe
from frappe import _
from frappe.utils import cint, flt, get_datetime_str
import time
from frappe.utils.background_jobs import enqueue


def execute(filters=None):
    columns = get_columns()
    
    # Check if we should use cached data (cache valid for 1 hour)
    cache_key = f"meher_creation_report_{get_cache_key(filters)}"
    cached_data = frappe.cache().get_value(cache_key)
    
    if cached_data:
        return columns, cached_data
    
    # Get data with pagination to avoid timeouts
    data = get_datas(filters=filters)
    
    # Cache the data for 1 hour
    frappe.cache().set_value(cache_key, data, expires_in_sec=3600)
    
    return columns, data


def get_cache_key(filters):
    """Generate a unique cache key based on filters"""
    if not filters:
        return "no_filters"
    
    key_parts = []
    for k, v in filters.items():
        if v:
            key_parts.append(f"{k}_{v}")
    
    return "_".join(key_parts) or "default"


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
    """Optimized data retrieval with proper batch processing and efficient queries"""
    try:
        # Build conditions for filtering
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
        
        # Process data in batches to avoid memory issues and timeouts
        page_size = 50  # Reduced page size for better performance
        offset = 0
        all_data = []
        
        while True:
            # Get batch of containers with LIMIT and OFFSET
            container_query = """
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
                    c.docstatus = 1
                    {conditions}
                ORDER BY 
                    c.posting_date DESC, c.container_no
                LIMIT {page_size} OFFSET {offset}
            """.format(conditions=conditions, page_size=page_size, offset=offset)
            
            containers = frappe.db.sql(container_query, filters, as_dict=1)
            
            if not containers:
                break  # No more data
            
            # Get all container names and numbers for this batch
            container_names = [c.container_name for c in containers]
            container_nos = [c.container for c in containers if c.container]
            
            # Get all supplier batch numbers in one query
            supplier_batch_dict = {}
            if container_nos:
                supplier_batch_query = """
                    SELECT 
                        custom_container_no, 
                        custom_supplier_batch_no 
                    FROM 
                        `tabBatch` 
                    WHERE 
                        custom_container_no IN ({})
                """.format(','.join(['%s'] * len(container_nos)))
                
                supplier_batches = frappe.db.sql(
                    supplier_batch_query, container_nos, as_dict=1
                )
                
                for batch in supplier_batches:
                    if batch.custom_container_no:
                        supplier_batch_dict[batch.custom_container_no] = batch.custom_supplier_batch_no
            
            # Get all cone and batch data in optimized queries
            if container_names:
                # Get cone data
                cones_query = """
                    SELECT 
                        parent, 
                        cone,
                        batch_id
                    FROM 
                        `tabBatch Items`
                    WHERE 
                        parent IN ({})
                """.format(','.join(['%s'] * len(container_names)))
                
                cones_data = frappe.db.sql(cones_query, container_names, as_dict=1)
                
                # Get batch quantities for all batch IDs at once
                batch_ids = [cone.batch_id for cone in cones_data if cone.batch_id]
                batch_qty_dict = {}
                
                if batch_ids:
                    batch_qty_query = """
                        SELECT 
                            name,
                            batch_qty
                        FROM 
                            `tabBatch`
                        WHERE 
                            name IN ({})
                    """.format(','.join(['%s'] * len(batch_ids)))
                    
                    batch_quantities = frappe.db.sql(batch_qty_query, batch_ids, as_dict=1)
                    
                    for batch in batch_quantities:
                        batch_qty_dict[batch.name] = flt(batch.batch_qty)
                
                # Process containers and build result
                container_data_dict = {}
                for container in containers:
                    container_data_dict[container.container_name] = container
                
                # Group cones by container and calculate totals
                container_cones = {}
                container_totals = {}
                
                for cone_item in cones_data:
                    parent = cone_item.parent
                    cone = cone_item.cone
                    batch_qty = batch_qty_dict.get(cone_item.batch_id, 0)
                    
                    if parent not in container_cones:
                        container_cones[parent] = {}
                        container_totals[parent] = 0
                    
                    if cone not in container_cones[parent]:
                        container_cones[parent][cone] = {'qty': 0, 'boxes': 0}
                    
                    container_cones[parent][cone]['qty'] += batch_qty
                    if batch_qty > 0:
                        container_cones[parent][cone]['boxes'] += 1
                    
                    container_totals[parent] += batch_qty
                
                # Build final result for this batch
                for container_name, cones_dict in container_cones.items():
                    container_info = container_data_dict.get(container_name)
                    if not container_info:
                        continue
                    
                    supplier_batch_no = supplier_batch_dict.get(container_info.container, "")
                    total_closing = container_totals.get(container_name, 0)
                    
                    for cone, cone_data in cones_dict.items():
                        row = {
                            'date': container_info.date,
                            'container': container_info.container,
                            'item': container_info.item,
                            'pulp': container_info.pulp,
                            'lusture': container_info.lusture,
                            'glue': container_info.glue,
                            'grade': container_info.grade,
                            'mer_no': container_info.mer_no,
                            'lot_no': container_info.lot_no,
                            'warehouse': container_info.warehouse,
                            'cross_section': container_info.cross_section,
                            'total_closing': total_closing,
                            'cone': cone,
                            'boxes': cone_data['boxes'],
                            'stock': cone_data['qty'],
                            'supplier_batch_no': supplier_batch_no,
                            'batch': ''  # Add if needed
                        }
                        all_data.append(row)
            
            offset += page_size
            
            # Add a small delay to prevent overwhelming the database
            if offset % 200 == 0:  # Every 4 batches
                time.sleep(0.1)
        
        return all_data
        
    except Exception as e:
        frappe.log_error(f"Error in Meher Creation Report: {str(e)}", "Meher Creation Report Error")
        return []