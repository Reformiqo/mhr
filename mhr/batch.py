import frappe

@frappe.whitelist()
def recalculate_batch_qty():
    batches = frappe.db.sql("""
        SELECT name FROM `tabBatch`
    """, as_dict=True)

    for batch in batches:
        actual_qty = get_batch_qty(batch.name)

        frappe.db.sql("""
            UPDATE `tabBatch`
            SET batch_qty = %s
            WHERE name = %s
        """, (actual_qty, batch.name))

    frappe.db.commit()
    return f"Recalculated {len(batches)} batches"


def get_batch_qty(batch_name):
    """
    Get actual batch qty from Serial and Batch Entry table.
    Inward transactions add qty, Outward transactions subtract qty.
    """
    result = frappe.db.sql("""
        SELECT COALESCE(SUM(
            CASE
                WHEN sbb.type_of_transaction = 'Outward' THEN -ABS(sbe.qty)
                ELSE ABS(sbe.qty)
            END
        ), 0) as qty
        FROM `tabSerial and Batch Entry` sbe
        INNER JOIN `tabSerial and Batch Bundle` sbb ON sbe.parent = sbb.name
        WHERE sbe.batch_no = %s
        AND sbb.docstatus = 1
        AND sbb.is_cancelled = 0
    """, (batch_name,), as_dict=True)

    return result[0].qty if result else 0


@frappe.whitelist()
def debug_batch_qty(batch_name):
    """
    Debug function to see Serial and Batch Entry data for a batch.
    """
    entries = frappe.db.sql("""
        SELECT
            sbe.name,
            sbe.parent,
            sbe.batch_no,
            sbe.qty,
            sbb.type_of_transaction,
            sbb.voucher_type,
            sbb.voucher_no,
            sbb.docstatus,
            sbb.is_cancelled
        FROM `tabSerial and Batch Entry` sbe
        INNER JOIN `tabSerial and Batch Bundle` sbb ON sbe.parent = sbb.name
        WHERE sbe.batch_no = %s
    """, (batch_name,), as_dict=True)

    return entries

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


@frappe.whitelist()
def cleanup_orphan_bundles_for_batch(batch_name):
    """
    Clean up orphan Serial and Batch Bundles for a specific batch.
    Keeps only the bundle linked to a valid (non-cancelled) Stock Ledger Entry.
    """
    kept_count, cancelled_count = _cleanup_orphan_bundles(batch_name)
    frappe.db.commit()
    return f"Batch {batch_name}: Kept {kept_count} bundles, Cancelled {cancelled_count} orphan bundles"


@frappe.whitelist()
def cleanup_orphan_bundles_all_batches():
    """
    Clean up orphan Serial and Batch Bundles for all batches.
    """
    batches = frappe.db.sql("""
        SELECT DISTINCT sbe.batch_no
        FROM `tabSerial and Batch Entry` sbe
        INNER JOIN `tabSerial and Batch Bundle` sbb ON sbe.parent = sbb.name
        WHERE sbb.is_cancelled = 0
    """, as_dict=True)

    total_cancelled = 0
    total_kept = 0

    for batch in batches:
        kept, cancelled = _cleanup_orphan_bundles(batch.batch_no)
        total_kept += kept
        total_cancelled += cancelled

    frappe.db.commit()
    return f"Total: Kept {total_kept} bundles, Cancelled {total_cancelled} orphan bundles across {len(batches)} batches"


def _cleanup_orphan_bundles(batch_name):
    """
    Internal function to clean up orphan bundles for a batch.
    Returns tuple (kept_count, cancelled_count)
    """
    # Get all Serial and Batch Bundles for this batch
    bundles = frappe.db.sql("""
        SELECT DISTINCT
            sbb.name as bundle_name,
            sbb.is_cancelled
        FROM `tabSerial and Batch Entry` sbe
        INNER JOIN `tabSerial and Batch Bundle` sbb ON sbe.parent = sbb.name
        WHERE sbe.batch_no = %s
        AND sbb.is_cancelled = 0
    """, (batch_name,), as_dict=True)

    cancelled_count = 0
    kept_count = 0

    for bundle in bundles:
        # Check if this bundle is linked to a valid (non-cancelled) Stock Ledger Entry
        valid_sle = frappe.db.sql("""
            SELECT name FROM `tabStock Ledger Entry`
            WHERE serial_and_batch_bundle = %s
            AND is_cancelled = 0
        """, (bundle.bundle_name,))

        if valid_sle:
            kept_count += 1
        else:
            frappe.db.set_value(
                "Serial and Batch Bundle",
                bundle.bundle_name,
                "is_cancelled",
                1
            )
            cancelled_count += 1

    return kept_count, cancelled_count


@frappe.whitelist()
def enqueue_cleanup_orphan_bundles():
    """
    Enqueue cleanup of orphan Serial and Batch Bundles for all batches.
    """
    frappe.enqueue(cleanup_orphan_bundles_all_batches, queue="long")
    return "Cleanup job enqueued"


@frappe.whitelist()
def get_orphan_bundles_for_batch(batch_name):
    """
    Get list of orphan Serial and Batch Bundles for a batch (for debugging).
    """
    bundles = frappe.db.sql("""
        SELECT DISTINCT
            sbb.name as bundle_name,
            sbb.is_cancelled,
            sbb.docstatus,
            sbb.voucher_type,
            sbb.voucher_no,
            sbb.type_of_transaction,
            sbe.qty
        FROM `tabSerial and Batch Entry` sbe
        INNER JOIN `tabSerial and Batch Bundle` sbb ON sbe.parent = sbb.name
        WHERE sbe.batch_no = %s
        AND sbb.is_cancelled = 0
    """, (batch_name,), as_dict=True)

    result = []
    for bundle in bundles:
        # Check if linked to valid SLE
        valid_sle = frappe.db.sql("""
            SELECT name FROM `tabStock Ledger Entry`
            WHERE serial_and_batch_bundle = %s
            AND is_cancelled = 0
        """, (bundle.bundle_name,))

        bundle['has_valid_sle'] = bool(valid_sle)
        bundle['is_orphan'] = not bool(valid_sle)
        result.append(bundle)

    return result