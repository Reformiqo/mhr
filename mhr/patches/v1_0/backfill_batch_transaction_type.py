# Per Raj's batch-Transaction-Type spec (2026-06-24): backfill
# tabBatch.custom_transaction_type from the linked Container.
#
# Strategy: a derived map (container_no -> transaction_type) joined
# back onto tabBatch via custom_container_no. Container's
# transaction_type is uniform within a container_no in practice
# (Container autoname is format:{container_no}-{#} but the value
# itself doesn't vary by suffix), so MAX is a safe collapse.
#
# Idempotent — only updates rows whose custom_transaction_type is
# NULL or empty.
#
# Run guard: chunked in batches of 5000 to avoid lock-wait timeouts
# on the 295k-row tabBatch table.

import frappe


CHUNK_SIZE = 5000


def execute():
    # Skip if the destination column hasn't been created yet (fresh
    # site running patches before the Custom Field fixture imports).
    try:
        frappe.db.sql(
            "SELECT custom_transaction_type FROM `tabBatch` LIMIT 1"
        )
    except Exception:
        return

    # 1. Build (container_no -> transaction_type) map from Container.
    #    Limit to non-empty transaction_type values; ignore the rest.
    container_map = frappe.db.sql(
        """
        SELECT container_no, MAX(transaction_type) AS tt
        FROM `tabContainer`
        WHERE transaction_type IS NOT NULL AND transaction_type != ''
        GROUP BY container_no
        """,
        as_dict=True,
    )
    if not container_map:
        return

    # 2. Bucket the map by transaction_type so we issue one UPDATE per
    #    transaction_type (typically 2: VFY + HTY). UPDATE ... WHERE
    #    container_no IN (...) chunked at CHUNK_SIZE values per call.
    by_tt: dict[str, list[str]] = {}
    for row in container_map:
        by_tt.setdefault(row["tt"], []).append(row["container_no"])

    total_touched = 0
    for tt, container_nos in by_tt.items():
        for i in range(0, len(container_nos), CHUNK_SIZE):
            chunk = container_nos[i:i + CHUNK_SIZE]
            placeholders = ", ".join(["%s"] * len(chunk))
            frappe.db.sql(
                f"""
                UPDATE `tabBatch`
                SET custom_transaction_type = %s
                WHERE custom_container_no IN ({placeholders})
                  AND (custom_transaction_type IS NULL
                       OR custom_transaction_type = '')
                """,
                tuple([tt] + list(chunk)),
            )
            total_touched += frappe.db.sql("SELECT ROW_COUNT()")[0][0]
            frappe.db.commit()

    print(f"Backfilled custom_transaction_type on {total_touched} Batch rows.")
