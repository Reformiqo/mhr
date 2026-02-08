import frappe


def execute():
    """Add indexes on Batch table fields used in report filters."""
    indexes = [
        ("tabBatch", "custom_container_no"),
        ("tabBatch", "custom_lot_no"),
        ("tabBatch", "custom_cone"),
        ("tabBatch", "manufacturing_date"),
    ]
    for table, column in indexes:
        index_name = f"idx_{column}"
        if not frappe.db.sql(
            """SELECT 1 FROM information_schema.statistics
            WHERE table_schema = DATABASE()
            AND table_name = %s
            AND index_name = %s
            LIMIT 1""",
            (table, index_name),
        ):
            frappe.db.sql_ddl(f"ALTER TABLE `{table}` ADD INDEX `{index_name}` (`{column}`)")
