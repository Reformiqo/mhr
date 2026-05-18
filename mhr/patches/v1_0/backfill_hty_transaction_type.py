"""MI1-I39 — Backfill `transaction_type` on legacy docs.

The `transaction_type` Select(Normal/HTY) field was added in Phase 1.
Existing documents predate the field, so their column value is NULL.
The reports treat NULL as 'Normal' via IFNULL, but that's a crutch —
a clean backfill avoids the IFNULL overhead, makes the Normal filter
behavior obvious in the data, and lets us add the IFNULL crutch's
removal in a later patch.

Tables touched:
  - tabContainer
  - tabSales Order
  - tabDelivery Note
  - tabStock Entry
  - tabPrint Batch
  - tabDelivery Trip

Each UPDATE is guarded by IFNULL(transaction_type,'')='', so:
  - Already-set rows (Normal or HTY) are not overwritten.
  - Newly-created docs (which now default to 'Normal' via the Custom
    Field default) are unaffected.

Idempotent — re-running this patch is a no-op once it's run once.
"""

import frappe


# Order matters only for stable log output; the UPDATEs are independent.
TABLES = (
    "tabContainer",
    "tabSales Order",
    "tabDelivery Note",
    "tabStock Entry",
    "tabPrint Batch",
    "tabDelivery Trip",
)


def execute():
    for table in TABLES:
        # Schema may not have the column yet on benches that haven't run the
        # Custom Field fixture import (e.g. a fresh test bench). Skip silently
        # — bench migrate will re-run this patch after fixtures land.
        if not _column_exists(table, "transaction_type"):
            frappe.logger().info(
                f"[MI1-I39 backfill] skipping `{table}` — transaction_type column not present yet."
            )
            continue

        updated = frappe.db.sql(
            f"""
            UPDATE `{table}`
            SET transaction_type = 'Normal'
            WHERE IFNULL(transaction_type, '') = ''
            """
        )
        # MariaDB doesn't return affected-row count via this API; query
        # for diagnostic logging.
        remaining = frappe.db.sql(
            f"""
            SELECT COUNT(*) FROM `{table}`
            WHERE IFNULL(transaction_type, '') = ''
            """
        )[0][0]
        frappe.logger().info(
            f"[MI1-I39 backfill] `{table}` — remaining NULL/empty after UPDATE: {remaining}"
        )

    frappe.db.commit()


def _column_exists(table, column):
    rows = frappe.db.sql(
        """
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = DATABASE()
          AND table_name = %s
          AND column_name = %s
        LIMIT 1
        """,
        (table, column),
    )
    return bool(rows)
