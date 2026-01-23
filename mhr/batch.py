import frappe

@frappe.whitelist()
def recalculate_batch_qty():
    batches = frappe.db.sql("""
        SELECT name FROM `tabBatch`
        WHERE batch_qty > 30
    """, as_dict=True)

    for batch in batches:
        actual_qty = get_batch_qty(batch.name)

        # Update batch_qty directly
        frappe.db.sql("""
            UPDATE `tabBatch`
            SET batch_qty = %s
            WHERE name = %s
        """, (actual_qty, batch.name))

    frappe.db.commit()


def get_batch_qty(batch_name):
    """
    Get actual batch qty from Serial and Batch Bundle (v14+) and Stock Ledger Entry.
    """
    # Method 1: Get qty from Serial and Batch Entry (v14+ with Serial and Batch Bundle)
    sbb_qty = frappe.db.sql("""
        SELECT COALESCE(SUM(sbe.qty), 0) as qty
        FROM `tabSerial and Batch Entry` sbe
        INNER JOIN `tabSerial and Batch Bundle` sbb ON sbe.parent = sbb.name
        WHERE sbe.batch_no = %s
        AND sbb.docstatus = 1
        AND sbb.is_cancelled = 0
    """, (batch_name,), as_dict=True)

    if sbb_qty and sbb_qty[0].qty:
        return sbb_qty[0].qty

    # Method 2: Fallback to direct batch_no in Stock Ledger Entry (legacy)
    sle_qty = frappe.db.sql("""
        SELECT COALESCE(SUM(actual_qty), 0) as qty
        FROM `tabStock Ledger Entry`
        WHERE batch_no = %s
        AND is_cancelled = 0
    """, (batch_name,), as_dict=True)

    return sle_qty[0].qty if sle_qty else 0

@frappe.whitelist()
def enqueue_recalculate_batch_qty():
    frappe.enqueue(recalculate_batch_qty, queue="long")


@frappe.whitelist()
def recalculate_selected_batches(batch_names):
    import json
    if isinstance(batch_names, str):
        batch_names = json.loads(batch_names)

    for batch_name in batch_names:
        actual_qty = get_batch_qty(batch_name)

        # Update batch_qty directly
        frappe.db.sql("""
            UPDATE `tabBatch`
            SET batch_qty = %s
            WHERE name = %s
        """, (actual_qty, batch_name))

    frappe.db.commit()
    frappe.msgprint(f"Recalculated {len(batch_names)} batches")