# MI1-I63 (reopen, Raj 2026-06-29): existing Delivery Note Item rows
# still show custom_gross_weight = 0 in the UI even though the linked
# Batch has a positive Gross Weight, because the fetch_from (and the
# server-side backfill on DN.validate) only run when the DN is next
# saved. Historical rows stay stale until then.
#
# This one-shot heal SQL-updates DN Item.custom_gross_weight from
# Batch.custom_gross_weight for every row where:
#   * DN is non-cancelled (docstatus < 2)
#   * DN Item.batch_no is set
#   * DN Item.custom_gross_weight is 0 / NULL (don't clobber user overrides)
#   * The linked Batch has custom_gross_weight > 0
#
# Chunked update to stay safe on prod's DN Item scale. Idempotent.

import frappe


CHUNK_SIZE = 5000


def execute():
    # Guard: skip if either column is missing (fresh site before
    # fixtures land).
    try:
        frappe.db.sql("SELECT custom_gross_weight FROM `tabDelivery Note Item` LIMIT 1")
        frappe.db.sql("SELECT custom_gross_weight FROM `tabBatch` LIMIT 1")
    except Exception:
        return

    # Collect stale row names first — driving the UPDATE off a
    # pre-computed name list keeps each chunked write short and
    # explicit.
    stale = frappe.db.sql(
        """
        SELECT dni.name, b.custom_gross_weight AS gw
        FROM `tabDelivery Note Item` dni
        INNER JOIN `tabDelivery Note` dn ON dn.name = dni.parent
        INNER JOIN `tabBatch` b ON b.name = dni.batch_no
        WHERE dn.docstatus < 2
          AND dni.batch_no IS NOT NULL
          AND (dni.custom_gross_weight IS NULL OR dni.custom_gross_weight = 0)
          AND b.custom_gross_weight > 0
        """,
        as_dict=True,
    )
    if not stale:
        return

    total = 0
    for i in range(0, len(stale), CHUNK_SIZE):
        chunk = stale[i:i + CHUNK_SIZE]
        for row in chunk:
            frappe.db.set_value(
                "Delivery Note Item", row["name"],
                "custom_gross_weight", row["gw"],
                update_modified=False,
            )
            total += 1
        frappe.db.commit()

    print(f"Healed custom_gross_weight on {total} Delivery Note Item rows.")
