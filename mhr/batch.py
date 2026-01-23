import frappe

@frappe.whitelist()
def recalculate_batch_qty():
    batches = frappe.db.sql("""
        SELECT name FROM `tabBatch`
        WHERE batch_qty > 30
    """, as_dict=True)

    for batch in batches:
        # Get actual qty from Stock Ledger Entry
        result = frappe.db.sql("""
            SELECT COALESCE(SUM(actual_qty), 0) as qty
            FROM `tabStock Ledger Entry`
            WHERE batch_no = %s
            AND is_cancelled = 0
        """, (batch.name,), as_dict=True)

        actual_qty = result[0].qty if result else 0

        # Update batch_qty directly
        frappe.db.sql("""
            UPDATE `tabBatch`
            SET batch_qty = %s
            WHERE name = %s
        """, (actual_qty, batch.name))

    frappe.db.commit()

@frappe.whitelist()
def enqueue_recalculate_batch_qty():
    frappe.enqueue(recalculate_batch_qty, queue="long")


@frappe.whitelist()
def recalculate_selected_batches(batch_names):
    import json
    if isinstance(batch_names, str):
        batch_names = json.loads(batch_names)

    for batch_name in batch_names:
        doc = frappe.get_doc("Batch", batch_name)
        doc.recalculate_batch_qty()

    frappe.msgprint(f"Recalculated {len(batch_names)} batches")