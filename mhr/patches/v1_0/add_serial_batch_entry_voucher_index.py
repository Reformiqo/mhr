import frappe

TABLE = "tabSerial and Batch Entry"
INDEX_NAME = "idx_sbe_voucher"
COLUMNS = ("voucher_type", "voucher_no")


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


def columns_exist(table=TABLE, columns=COLUMNS):
    """MI1-I146: `voucher_type` / `voucher_no` (and `is_cancelled`) do not
    exist on this table's v15 schema at all — confirmed via a real
    DESCRIBE on this bench's own v15 site (columns present instead:
    is_outward, no voucher_type/voucher_no/is_cancelled). Frappe v16
    denormalised the voucher reference onto every entry row; v15 tracks it
    only on the parent Serial and Batch Bundle. This app is pinned to v15
    today (pyproject.toml) with `patches.txt` shared across every site
    regardless of version, so an unconditional CREATE INDEX on columns
    that may not exist would crash `bench migrate` the moment this ships —
    check first, no-op cleanly on a site whose schema doesn't have them."""
    present = frappe.db.sql(
        """SELECT column_name FROM information_schema.columns
           WHERE table_schema = DATABASE() AND table_name = %s
             AND column_name IN %s""",
        (table, tuple(columns)),
    )
    return {c for (c,) in present} == set(columns)


def execute():
    """MI1-I146 (2026-09-15, "why is cancelling this Delivery Note taking so
    long"): ERPNext's own document-cancellation flow (Serial and Batch
    Bundle -> voucher reversal) updates every entry belonging to the voucher
    being cancelled:

        UPDATE `tabSerial and Batch Entry`
        SET is_cancelled = 1
        WHERE voucher_no = %s AND voucher_type = %s

    Neither column was ever indexed on this table (only `batch_no` /
    `parent`, added by MI1-I119's own add_serial_batch_bundle_status_index
    patch for a different query shape) -- EXPLAIN confirmed a full table
    scan, `type: ALL`, ~1.19M rows read, for this exact query on this
    bench. mhr's own "Cancel in Background" feature (MI1-I26's sibling for
    Delivery Note) runs this once PER ROW being cancelled -- a 217-row note
    live-caught mid-cancel was still on row 1 after 6+ minutes, each
    full-table-scan UPDATE taking multiple seconds, live-confirmed via
    information_schema.processlist and EXPLAIN before this patch existed.
    This is core ERPNext's own cancel machinery, not anything mhr's hooks
    do — the fix belongs in an index, the same class of fix MI1-I119
    already applied to the sibling table for a different join shape.

    Adding this index CONCURRENTLY with an active, continuous stream of
    writes against this exact table (i.e. mid-cancel of a large document)
    does not work even with ALGORITHM=INPLACE, LOCK=NONE — a live attempt
    sat "Waiting for table metadata lock" for 8+ minutes and was
    (correctly) killed rather than left to potentially queue ahead of and
    stall the in-flight cancel's own next UPDATE. Run this patch (like any
    schema change here) when nothing is actively hammering the table.

    A no-op on a v15 site (see columns_exist's own docstring): this table's
    v15 schema has no `voucher_type` / `voucher_no` columns to index at
    all, so there is nothing to fix there yet — the slow query itself is
    unreachable code on v15 (the site pinned in pyproject.toml today).
    """
    if not columns_exist():
        return
    if index_exists():
        return
    cols = ", ".join(f"`{c}`" for c in COLUMNS)
    frappe.db.sql_ddl(f"CREATE INDEX `{INDEX_NAME}` ON `{TABLE}` ({cols})")
