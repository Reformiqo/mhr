import frappe

TABLE = "tabSerial and Batch Bundle"
INDEX_NAME = "idx_sbb_voucher"
COLUMNS = ("voucher_no", "voucher_type")


def index_exists(table=TABLE, index_name=INDEX_NAME):
    return bool(
        frappe.db.sql(
            """SELECT 1 FROM information_schema.statistics
               WHERE table_schema = DATABASE()
                 AND table_name = %s
                 AND index_name = %s
               LIMIT 1""",
            (table, index_name),
        )
    )


def execute():
    # ERPNext cancels bundles WHERE voucher_no=… but never indexes voucher_no; unindexed, that locking UPDATE scans every Delivery Note bundle.
    if index_exists():
        return
    cols = ", ".join(f"`{c}`" for c in COLUMNS)
    frappe.db.sql_ddl(f"CREATE INDEX `{INDEX_NAME}` ON `{TABLE}` ({cols})")
