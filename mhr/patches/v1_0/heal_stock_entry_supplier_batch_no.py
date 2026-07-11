# MI1-I50 (reopen, Raj 2026-07-10): Stock Entry Detail's
# custom_supplier_batch_no was never populated because the Custom
# Field had no fetch_from. Users saw blank Supplier Batch No in
# Stock Entry Items and in the print format for every historical row.
#
# The fetch_from = 'batch_no.custom_supplier_batch_no' is now in place
# going forward. This heal fills the historical rows in one shot.
#
# Idempotent — only touches rows where the field is 0 / NULL / empty
# and the linked Batch has a non-empty custom_supplier_batch_no.

import frappe


CHUNK_SIZE = 5000


def execute():
    # Guard on the columns existing (fresh site before fixtures).
    try:
        frappe.db.sql("SELECT custom_supplier_batch_no FROM `tabStock Entry Detail` LIMIT 1")
        frappe.db.sql("SELECT custom_supplier_batch_no FROM `tabBatch` LIMIT 1")
    except Exception:
        return

    stale = frappe.db.sql(
        """
        SELECT sed.name, b.custom_supplier_batch_no AS sbn
        FROM `tabStock Entry Detail` sed
        INNER JOIN `tabBatch` b ON b.name = sed.batch_no
        WHERE sed.batch_no IS NOT NULL
          AND (sed.custom_supplier_batch_no IS NULL OR sed.custom_supplier_batch_no = '')
          AND b.custom_supplier_batch_no IS NOT NULL
          AND b.custom_supplier_batch_no != ''
        """,
        as_dict=True,
    )
    if not stale:
        return

    total = 0
    for i in range(0, len(stale), CHUNK_SIZE):
        for row in stale[i:i + CHUNK_SIZE]:
            frappe.db.set_value(
                "Stock Entry Detail", row["name"],
                "custom_supplier_batch_no", row["sbn"],
                update_modified=False,
            )
            total += 1
        frappe.db.commit()

    print(f"Healed custom_supplier_batch_no on {total} Stock Entry Detail rows.")
