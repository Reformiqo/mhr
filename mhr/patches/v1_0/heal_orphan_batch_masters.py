"""MI1-I41 — Heal orphan Batch masters (pre-MI1-I36-F2 corruption).

The MI1-I36 F2 fix (commit 5066c80, before_submit + on_cancel guards)
landed on FC after a window where duplicate (container_no, lot_no)
Containers existed. The pre-fix `on_cancel` did:
    DELETE FROM `tabBatch` WHERE name = %s
without verifying that another non-cancelled Container also referenced
the batch_id — so cancelling a duplicate wiped the Batch masters that
the SURVIVING Submitted Container still depended on. Result: orphan
`tabBatch Items` child rows pointing at non-existent Batch masters.

Symptoms reported by Raj:
  - MI1-I36 (MCJC-1361 lot 21112025): 250 missing Batches → Container
    Report missed the lot row, Batch form opened blank.
  - MI1-I41 (MCJC-1538 lots 04012026/12012026/19012026 + one 11012026):
    Container Report only showed 1 of 5 expected rows because 4 lots'
    Batch masters were missing.

This patch heals by:

  1. Finding every Submitted Container whose `tabBatch Items` child
     rows reference a batch_id that doesn't exist in `tabBatch`.
  2. For each orphan, creating the Batch master via `frappe.new_doc("Batch")`
     using the same field mapping as `Container.create_batches`
     (container.py:197) — item, stock_uom (fallback to Item.stock_uom),
     custom_container_no, custom_cone, custom_lot_no, the quality
     fields from the Container header, etc.
  3. Pinning `batch_qty` from the child row's `qty` field so the
     Container Report aggregates correctly without waiting for an SLE
     replay.

Idempotent: skips batch_ids that already exist. Re-running this patch
is a no-op once it's done.

Safety: only INSERTs; never deletes or overwrites existing data.
"""

import frappe
from frappe.utils import flt, getdate


def execute():
    # Find every (Container, batch_id) where the Container is submitted
    # but the Batch master is missing.
    orphan_rows = frappe.db.sql(
        """
        SELECT bi.parent AS container, bi.batch_id, bi.qty AS child_qty,
               bi.cone AS child_cone, bi.supplier_batch_no, bi.uom AS child_uom,
               bi.item AS child_item
        FROM `tabBatch Items` bi
        JOIN `tabContainer` c ON c.name = bi.parent
        WHERE bi.parenttype = 'Container'
          AND c.docstatus = 1
          AND bi.batch_id IS NOT NULL
          AND bi.batch_id != ''
          AND NOT EXISTS (
              SELECT 1 FROM `tabBatch` b WHERE b.name = bi.batch_id
          )
        """,
        as_dict=True,
    )
    if not orphan_rows:
        frappe.logger().info(
            "[MI1-I41 heal] no orphan Batch masters found — nothing to heal."
        )
        return

    # Cache Container headers so we don't refetch per child row.
    container_cache = {}
    created = 0
    errors = []

    for row in orphan_rows:
        cname = row.container
        if cname not in container_cache:
            container_cache[cname] = frappe.db.get_value(
                "Container", cname,
                [
                    "container_no", "lot_no", "glue", "lusture", "pulp",
                    "grade", "fsc", "cross_section", "notes",
                    "production_date", "merge_no", "warehouse",
                ],
                as_dict=True,
            )
        c = container_cache[cname]
        if not c:
            errors.append((row.batch_id, "container header missing"))
            continue

        # Some legacy child rows have whitespace-padded item codes
        # (e.g. ` 30S ECOSHINE RING SPUN YARN` with a leading space)
        # while the Item master is the stripped form. Frappe's Link
        # validation fails on the un-stripped value with a cryptic
        # "cannot unpack non-iterable NoneType" — strip first.
        item_code = (row.child_item or "").strip()
        if not item_code or not frappe.db.exists("Item", item_code):
            errors.append((row.batch_id, f"Item not found: {row.child_item!r}"))
            continue

        try:
            b = frappe.new_doc("Batch")
            b.item = item_code
            b.batch_id = row.batch_id
            b.stock_uom = row.child_uom or frappe.db.get_value(
                "Item", item_code, "stock_uom"
            )
            b.custom_supplier_batch_no = row.supplier_batch_no
            b.custom_container_no = c.container_no
            b.custom_cone = row.child_cone
            b.custom_glue = c.glue
            b.custom_lusture = c.lusture
            b.custom_grade = c.grade
            b.custom_pulp = c.pulp
            b.custom_fsc = c.fsc
            b.custom_cross_section = c.cross_section
            b.custom_notes = c.notes
            b.custom_production_date = (
                getdate(c.production_date) if c.production_date else None
            )
            b.custom_merge_no = c.merge_no
            b.custom_warehouse = c.warehouse
            b.custom_lot_no = c.lot_no
            b.save(ignore_permissions=True)
            # batch_qty mirror — caller's container row was the source of truth
            # for the cone weight; SLE replay isn't reliable for orphan heals.
            frappe.db.set_value(
                "Batch", row.batch_id, "batch_qty", flt(row.child_qty)
            )
            created += 1
        except Exception:
            errors.append((row.batch_id, frappe.get_traceback()))

    frappe.db.commit()

    frappe.logger().info(
        f"[MI1-I41 heal] orphans found: {len(orphan_rows)} | "
        f"created: {created} | errors: {len(errors)}"
    )
    for batch_id, err in errors[:5]:
        frappe.logger().error(f"[MI1-I41 heal] {batch_id}: {err[:200]}")
