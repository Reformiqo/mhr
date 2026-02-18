import frappe


def execute():
    # Custom fields: change fieldtype from Data to Int
    custom_fields = [
        "Batch-custom_cone",
        "Delivery Note-custom_cone",
        "Delivery Note-custom_total_cone",
        "Delivery Note Item-custom_cone",
        "Purchase Receipt Item-custom_cone",
        "Stock Entry Detail-custom_cone",
        "Stock Entry-custom_total_cone",
    ]

    for cf_name in custom_fields:
        if frappe.db.exists("Custom Field", cf_name):
            frappe.db.set_value("Custom Field", cf_name, "fieldtype", "Int")

    # App doctypes: Batch Items and List Batches (cone field)
    for dt in ("Batch Items", "List Batches"):
        if frappe.db.exists("DocType", dt):
            frappe.db.sql("""
                UPDATE `tabDocField`
                SET fieldtype = 'Int'
                WHERE parent = %s AND fieldname = 'cone'
            """, (dt,))

    # Round decimal values to whole numbers in all affected tables
    tables = {
        "tabBatch": "custom_cone",
        "tabDelivery Note": "custom_cone",
        "tabDelivery Note": "custom_total_cone",
        "tabDelivery Note Item": "custom_cone",
        "tabPurchase Receipt Item": "custom_cone",
        "tabStock Entry Detail": "custom_cone",
        "tabStock Entry": "custom_total_cone",
        "tabBatch Items": "cone",
        "tabList Batches": "cone",
    }

    for table, column in tables.items():
        try:
            frappe.db.sql("""
                UPDATE `{table}`
                SET `{column}` = ROUND(`{column}`)
                WHERE `{column}` IS NOT NULL
                AND `{column}` != ROUND(`{column}`)
            """.format(table=table, column=column))
        except Exception:
            pass

    # Delivery Note has two cone columns - handle custom_total_cone separately
    try:
        frappe.db.sql("""
            UPDATE `tabDelivery Note`
            SET `custom_total_cone` = ROUND(`custom_total_cone`)
            WHERE `custom_total_cone` IS NOT NULL
            AND `custom_total_cone` != ROUND(`custom_total_cone`)
        """)
    except Exception:
        pass

    frappe.db.commit()
    frappe.clear_cache()
