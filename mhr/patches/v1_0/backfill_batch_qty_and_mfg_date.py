# MI1 (Raj 2026-07-10): the HTY Select-Batch popup on DN was showing
# '-' for Batch Qty and Mfg Date because Container.create_batches
# (and its duplicate in mhr.utilis.create_batches) never copied
#   Batch Items.qty  → Batch.batch_qty
#   Container.posting_date → Batch.manufacturing_date
# The going-forward fix lands in this same release; this patch
# backfills every existing Batch row whose master carries 0 / NULL
# but whose Batch Items row has a positive qty.
#
# Chunked at 5000 rows per commit to stay safe under lock-wait on
# the 295k-row tabBatch table. Idempotent.

import frappe


CHUNK_SIZE = 5000


def execute():
    try:
        frappe.db.sql("SELECT batch_qty, manufacturing_date FROM `tabBatch` LIMIT 1")
        frappe.db.sql("SELECT qty FROM `tabBatch Items` LIMIT 1")
    except Exception:
        return

    # Join: Batch Items -> Batch. We ALSO join tabContainer so we can
    # copy posting_date onto Batch.manufacturing_date.
    rows = frappe.db.sql(
        """
        SELECT
            bi.batch_id AS name,
            MAX(bi.qty) AS qty,
            MAX(c.posting_date) AS posting_date
        FROM `tabBatch Items` bi
        INNER JOIN `tabBatch` b ON b.name = bi.batch_id
        INNER JOIN `tabContainer` c ON c.name = bi.parent
        WHERE bi.qty > 0
          AND (
            (b.batch_qty IS NULL OR b.batch_qty = 0)
            OR b.manufacturing_date IS NULL
          )
        GROUP BY bi.batch_id
        """,
        as_dict=True,
    )
    if not rows:
        return

    touched = 0
    for i in range(0, len(rows), CHUNK_SIZE):
        for r in rows[i:i + CHUNK_SIZE]:
            update = {}
            # Only fill if actually empty on the master.
            master = frappe.db.get_value(
                "Batch", r["name"], ["batch_qty", "manufacturing_date"], as_dict=True
            ) or {}
            if not master.get("batch_qty"):
                update["batch_qty"] = r["qty"]
            if not master.get("manufacturing_date") and r.get("posting_date"):
                update["manufacturing_date"] = r["posting_date"]
            if update:
                frappe.db.set_value(
                    "Batch", r["name"], update, update_modified=False
                )
                touched += 1
        frappe.db.commit()

    print(f"Backfilled batch_qty / manufacturing_date on {touched} Batch rows.")
