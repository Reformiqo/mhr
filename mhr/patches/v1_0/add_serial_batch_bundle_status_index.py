import frappe

# (table, index name, columns)
INDEXES = [
    ("tabSerial and Batch Bundle", "idx_sbb_name_status", ("name", "docstatus", "is_cancelled")),
    ("tabSerial and Batch Entry", "idx_sbe_batch_cover", ("batch_no", "parent", "warehouse", "qty")),
]
INDEX_NAME = INDEXES[0][1]
TABLE = INDEXES[0][0]


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
    """MI1-I119: covering indexes for the join every live-stock lookup makes.

    The Stock Sheet (Balance Report) — and every other balance helper in the
    app — sums `Serial and Batch Entry.qty` per batch, keeping only rows whose
    bundle is submitted and not cancelled:

        JOIN `tabSerial and Batch Bundle` sbb ON sbb.name = sbe.parent
        WHERE sbb.docstatus = 1 AND sbb.is_cancelled = 0

    On this site that join was the whole cost of the report: ~850K entry rows
    read from the clustered table, each looked up in the 380K-row bundle
    table through its primary key, so a full-range run spent 73 of its 85
    seconds there (and frappe flipped the report into prepared-report mode
    once a run passed 15 s). With both sides answered from indexes — the
    entry's `(batch_no, parent, warehouse, qty)` and the bundle's `(name,
    docstatus, is_cancelled)` — the same stage takes ~4 s.
    `get_batch_warehouse_balances` names the bundle index through FORCE INDEX
    (the optimizer keeps preferring the primary key) only once this patch has
    created it; the entry index the optimizer picks by itself.
    """
    for table, index_name, columns in INDEXES:
        if index_exists(table, index_name):
            continue
        cols = ", ".join(f"`{c}`" for c in columns)
        frappe.db.sql_ddl(f"CREATE INDEX `{index_name}` ON `{table}` ({cols})")
