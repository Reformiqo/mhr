"""2026-09-06 — Batches created by a Job Work Received entry (MI1-I50) had
their master `batch_qty` preset from the row, and ERPNext's incremental
update_batch_qty added the posted qty on top: prod MCL-32-.-1 received 20 kg
and read 40, so the Delivery Note's cone arithmetic wrote 40 into the row and
submit failed with negative stock. Set the master back to the bundle balance
on every such batch. Idempotent; the preset is gone from create_receive_batches.
"""
import frappe


def execute():
    from mhr.utilis import heal_receive_batch_qty

    fixed = heal_receive_batch_qty()
    if fixed:
        frappe.db.commit()
        print(f"heal_receive_batch_qty: corrected {len(fixed)} batch(es): {', '.join(fixed[:10])}")
