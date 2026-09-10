import frappe

TABLE = "tabDelivery Note Item"
INDEX_NAME = "idx_dni_container_lot"
COLUMNS = ("custom_container_no", "custom_lot_no")


def index_exists():
    return bool(
        frappe.db.sql(
            """SELECT 1 FROM information_schema.statistics
               WHERE table_schema = DATABASE()
                 AND table_name = %s
                 AND index_name = %s
               LIMIT 1""",
            (TABLE, INDEX_NAME),
        )
    )


def execute():
    """MI1-I135: Stock Sheet (Balance Report) v2's Out Qty / GR Received
    movement query filters Delivery Note Item by custom_container_no (and,
    when the report's own Container/Lot filters are set, custom_lot_no too)
    — neither column carries an index, so MariaDB full-scans the whole
    327K-row table for a single container's Delivery lookup (~3.5 s, the
    entire cost of an otherwise sub-second narrow report run). Composite so
    a container-only lookup (the leftmost column) is covered too.
    """
    if index_exists():
        return
    cols = ", ".join(f"`{c}`" for c in COLUMNS)
    frappe.db.sql_ddl(f"CREATE INDEX `{INDEX_NAME}` ON `{TABLE}` ({cols})")
