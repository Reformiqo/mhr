# MI1 (Raj 2026-07-10): the HTY Select-Batch popup on DN was showing
# '-' for Batch Qty and Mfg Date because Container.create_batches
# (and its duplicate in mhr.utilis.create_batches) never copied
#   Batch Items.qty  → Batch.batch_qty
#   Container.posting_date → Batch.manufacturing_date
# The going-forward fix lands in this same release; this patch
# backfills every existing Batch row.
#
# Scaling strategy: iterate CONTAINER-BY-CONTAINER (there are ~ tens of
# thousands of Containers, MUCH smaller than the 295k-row tabBatch).
# For each Container, we already have its posting_date + its batches
# child table, so no giant join is needed and MariaDB's MAX_JOIN_SIZE
# never fires.
#
# Idempotent — only touches Batches whose master fields are 0 / NULL.

import frappe


CHUNK_SIZE = 1000  # containers per commit


def execute():
    try:
        frappe.db.sql("SELECT batch_qty, manufacturing_date FROM `tabBatch` LIMIT 1")
        frappe.db.sql("SELECT qty FROM `tabBatch Items` LIMIT 1")
    except Exception:
        return

    containers = frappe.db.sql(
        "SELECT name, posting_date FROM `tabContainer`",
        as_dict=True,
    )
    total = 0
    for i in range(0, len(containers), CHUNK_SIZE):
        for c in containers[i:i + CHUNK_SIZE]:
            batch_items = frappe.db.sql(
                """SELECT batch_id, qty
                   FROM `tabBatch Items`
                   WHERE parent = %s AND qty > 0""",
                (c["name"],),
                as_dict=True,
            )
            for bi in batch_items:
                # Master row's current values.
                master = frappe.db.get_value(
                    "Batch", bi["batch_id"],
                    ["batch_qty", "manufacturing_date"],
                    as_dict=True,
                )
                if not master:
                    continue
                update = {}
                if not master.get("batch_qty"):
                    update["batch_qty"] = bi["qty"]
                if not master.get("manufacturing_date") and c.get("posting_date"):
                    update["manufacturing_date"] = c["posting_date"]
                if update:
                    frappe.db.set_value(
                        "Batch", bi["batch_id"], update,
                        update_modified=False,
                    )
                    total += 1
        frappe.db.commit()

    print(f"Backfilled batch_qty / manufacturing_date on {total} Batch rows.")
