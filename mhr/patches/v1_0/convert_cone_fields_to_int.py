import frappe


def execute():
    # First: clean data in ALL cone columns so ALTER TABLE won't fail
    # The columns are currently varchar (Data type) with values like '6.0', '6', '', etc.
    # Convert to clean integers: '6.0' -> '6', '' -> '0', NULL -> '0'
    columns = [
        ("tabBatch", "custom_cone"),
        ("tabDelivery Note", "custom_cone"),
        ("tabDelivery Note", "custom_total_cone"),
        ("tabDelivery Note Item", "custom_cone"),
        ("tabPurchase Receipt Item", "custom_cone"),
        ("tabStock Entry Detail", "custom_cone"),
        ("tabStock Entry", "custom_total_cone"),
        ("tabBatch Items", "cone"),
        ("tabList Batches", "cone"),
    ]

    for table, column in columns:
        try:
            # Set empty/null to '0', then round any decimals like '6.0' to '6'
            frappe.db.sql("""
                UPDATE `{table}`
                SET `{column}` = '0'
                WHERE `{column}` IS NULL OR `{column}` = ''
            """.format(table=table, column=column))

            frappe.db.sql("""
                UPDATE `{table}`
                SET `{column}` = CAST(ROUND(CAST(`{column}` AS DECIMAL(20,2))) AS SIGNED)
            """.format(table=table, column=column))
        except Exception:
            pass

    # Now update Custom Field definitions to Int
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

    frappe.db.commit()
    frappe.clear_cache()
