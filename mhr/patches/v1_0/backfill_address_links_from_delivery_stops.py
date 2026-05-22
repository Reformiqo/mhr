"""MI1-I31 v2 — backfill Address ↔ Customer links from past Delivery Stops.

Background:
  Many existing Address rows on Meher's bench have an EMPTY `links`
  child table — i.e. no Dynamic Link row tying them back to a Customer.
  That's why Raj's Delivery Trip Stops kept showing a blank Address Name
  even after he picked an Address: `frappe.contacts.get_default_address`
  needs that Dynamic Link row to discover a customer's addresses.

What this patch does:
  Looks at every (`stop.customer`, `stop.address`) pair across both
  submitted and draft Delivery Trips on the bench. For each pair, if
  no `tabDynamic Link` row exists tying that Address to that Customer,
  INSERT one. The forward-fix in `fill_default_addresses_on_delivery_trip`
  prevents new orphaned addresses; this patch heals what's already there.

Idempotent:
  - Pre-check via frappe.db.exists skips pairs that already have the link.
  - Re-running is a no-op.

Safety:
  - INSERT-only; never deletes or overwrites existing Dynamic Link rows.
  - If the Address row no longer exists (stale stop.address), skips
    silently — no Trip save was ever blocked by this anyway.
"""

import frappe


def execute():
    # Find every (customer, address) pair that appears on a Delivery Stop
    # but DOESN'T already have a Dynamic Link row.
    pairs = frappe.db.sql(
        """
        SELECT DISTINCT ds.customer, ds.address
        FROM `tabDelivery Stop` ds
        WHERE ds.customer IS NOT NULL AND ds.customer != ''
          AND ds.address  IS NOT NULL AND ds.address  != ''
          AND NOT EXISTS (
              SELECT 1 FROM `tabDynamic Link` dl
              WHERE dl.parent = ds.address
                AND dl.parenttype = 'Address'
                AND dl.parentfield = 'links'
                AND dl.link_doctype = 'Customer'
                AND dl.link_name = ds.customer
          )
        """,
        as_dict=True,
    )
    if not pairs:
        frappe.logger().info(
            "[MI1-I31 v2 backfill] no missing Address↔Customer links — nothing to backfill."
        )
        return

    linked = 0
    skipped_missing_addr = 0
    errors = []

    for row in pairs:
        # Confirm the Address row still exists (a Stop may point at a
        # deleted Address).
        if not frappe.db.exists("Address", row.address):
            skipped_missing_addr += 1
            continue
        try:
            addr_doc = frappe.get_doc("Address", row.address)
            addr_doc.append("links", {
                "link_doctype": "Customer",
                "link_name": row.customer,
            })
            addr_doc.save(ignore_permissions=True)
            linked += 1
        except Exception:
            errors.append((row.address, row.customer, frappe.get_traceback()))

    frappe.db.commit()

    frappe.logger().info(
        f"[MI1-I31 v2 backfill] pairs found: {len(pairs)} | "
        f"linked: {linked} | skipped (missing Address): {skipped_missing_addr} | "
        f"errors: {len(errors)}"
    )
    for addr, cust, err in errors[:5]:
        frappe.logger().error(
            f"[MI1-I31 v2 backfill] failed {addr} → {cust}: {err[:200]}"
        )
