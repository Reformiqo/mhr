# MI1-I63 (reopen 2026-06-29): Container.create_batches used to skip
# custom_gross_weight when propagating Batch Items -> Batch master.
# All existing Batches created before that fix carry 0 in
# tabBatch.custom_gross_weight while the source value still sits on
# tabBatch Items.custom_gross_weight.
#
# Backfill copies the Batch Items value onto the Batch master, keying
# on batch.batch_id -> Batch Items.batch_id. Chunked at 5000 rows to
# match the tabBatch scale (295k+ rows on prod) and stay safe under
# lock-wait limits.
#
# Idempotent: only touches Batches whose custom_gross_weight is 0 AND
# whose linked Batch Items row has a positive value.

import frappe


CHUNK_SIZE = 5000


def execute():
    # Skip if either column is missing (fresh site running before the
    # Custom Field / doctype fixtures land).
    try:
        frappe.db.sql(
            "SELECT custom_gross_weight FROM `tabBatch` LIMIT 1"
        )
        frappe.db.sql(
            "SELECT custom_gross_weight FROM `tabBatch Items` LIMIT 1"
        )
    except Exception:
        return

    # Batches whose master GW is 0 but Batch Items row carries a positive
    # value. `Batch Items.batch_id` holds the Batch.name (per the child
    # table schema); collapse to distinct pairs so multiple Batch Items
    # rows referencing the same batch don't split.
    candidates = frappe.db.sql(
        """
        SELECT bi.batch_id AS name, MAX(bi.custom_gross_weight) AS gw
        FROM `tabBatch Items` bi
        INNER JOIN `tabBatch` b ON b.name = bi.batch_id
        WHERE bi.custom_gross_weight > 0
          AND (b.custom_gross_weight IS NULL OR b.custom_gross_weight = 0)
        GROUP BY bi.batch_id
        """,
        as_dict=True,
    )
    if not candidates:
        return

    total = 0
    for i in range(0, len(candidates), CHUNK_SIZE):
        chunk = candidates[i:i + CHUNK_SIZE]
        for row in chunk:
            frappe.db.set_value(
                "Batch", row["name"],
                "custom_gross_weight", row["gw"],
                update_modified=False,
            )
            total += 1
        frappe.db.commit()

    print(f"Backfilled custom_gross_weight on {total} Batch rows.")
