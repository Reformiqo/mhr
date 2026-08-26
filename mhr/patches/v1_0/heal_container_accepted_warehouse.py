"""MI1-I103 — restore Container.set_warehouse from the Purchase Receipt.

`update_batch_warehouse_on_stock_entry` used to write the Stock Entry's target
warehouse into Container.set_warehouse, so a Send to Subcontractor rewrote the
container's "Accepted Warehouse" to the subcontractor's. That field is not a
live location: Container Inward posts its Purchase Receipt and every Serial and
Batch Bundle to it, and Stock Sheet (Balance Report) renders it as the
"Accepted Warehouse" column. The hooks are gone; this repairs what they changed.

The original survives on the inward Purchase Receipt, which is submitted and
immutable — container.py sets `custom_container_no` to the Container docname and
posts every item to `set_warehouse`.

Candidates are submitted Containers whose free-text `warehouse` note is
byte-for-byte a real Warehouse name. That is the hooks' fingerprint (they wrote
both fields in one call) and it keeps this off untouched containers and off a
90k-row Purchase Receipt scan.

The free-text notes beside it — Container.warehouse, Batch.custom_warehouse,
Batch Items.warehouse — have no such source and are handled separately, by
heal_container_location_notes.

Idempotent: a second run finds every container already matching its receipt.
"""

import frappe

BATCH_SIZE = 200

# Single line on purpose: frappe.log_error swaps title and message when the
# title contains a newline, which would file the entry under the wrong heading.
LOG_TITLE = "MI1-I103 Container accepted-warehouse heal"


def execute():
    candidates = _damaged_containers()
    if not candidates:
        _report(scanned=0, healed=0, already_correct=0, no_receipt=0, ambiguous=0)
        return

    authoritative = _accepted_warehouse_from_purchase_receipts(
        [row["name"] for row in candidates]
    )

    healed = already_correct = no_receipt = ambiguous = 0
    pending = 0

    for row in candidates:
        resolved = authoritative.get(row["name"])
        if resolved is _AMBIGUOUS:
            ambiguous += 1
            continue
        if not resolved:
            no_receipt += 1
            continue
        if (row["set_warehouse"] or "") == resolved:
            already_correct += 1
            continue

        frappe.db.set_value(
            "Container", row["name"], "set_warehouse", resolved, update_modified=False
        )
        healed += 1
        pending += 1

        # Commit in slices so the Desk stays responsive and a failure does not
        # roll back work already proven good.
        if pending >= BATCH_SIZE:
            frappe.db.commit()
            pending = 0

    if pending:
        frappe.db.commit()

    _report(
        scanned=len(candidates),
        healed=healed,
        already_correct=already_correct,
        no_receipt=no_receipt,
        ambiguous=ambiguous,
    )


def _damaged_containers():
    """Submitted Containers carrying the hooks' fingerprint. The INNER JOIN on
    `tabWarehouse` is the "this note is actually a warehouse name" test."""
    return frappe.db.sql(
        """
        SELECT c.name, c.set_warehouse, c.warehouse
        FROM `tabContainer` c
        INNER JOIN `tabWarehouse` w ON w.name = c.warehouse
        WHERE c.docstatus = 1
        """,
        as_dict=True,
    )


# Sentinel: the container's Purchase Receipts disagree about the warehouse, so
# there is no single original to restore. Distinct from "no receipt found".
_AMBIGUOUS = object()


def _accepted_warehouse_from_purchase_receipts(container_names):
    """Container docname -> the warehouse its inward Purchase Receipt posted
    to, or _AMBIGUOUS when its receipts name more than one."""
    if not container_names:
        return {}

    placeholders = ", ".join(["%s"] * len(container_names))
    rows = frappe.db.sql(
        f"""
        SELECT pr.custom_container_no AS container, pri.warehouse AS warehouse
        FROM `tabPurchase Receipt` pr
        INNER JOIN `tabPurchase Receipt Item` pri ON pri.parent = pr.name
        WHERE pr.custom_container_no IN ({placeholders})
          AND pr.docstatus = 1
          AND IFNULL(pr.is_return, 0) = 0
          AND IFNULL(pri.warehouse, '') != ''
        GROUP BY pr.custom_container_no, pri.warehouse
        """,
        tuple(container_names),
        as_dict=True,
    )

    seen = {}
    for row in rows:
        seen.setdefault(row["container"], set()).add(row["warehouse"])

    return {
        container: (next(iter(warehouses)) if len(warehouses) == 1 else _AMBIGUOUS)
        for container, warehouses in seen.items()
    }


def _report(scanned, healed, already_correct, no_receipt, ambiguous):
    """Counts go to Error Log as well as the bench log — on Frappe Cloud there
    is no shell to tail, and the Desk is searchable by the title above."""
    summary = (
        f"Scanned        : {scanned} submitted Containers whose Warehouse note "
        "holds a real Warehouse name\n"
        f"Healed         : {healed} (set_warehouse restored from the Purchase Receipt)\n"
        f"Already correct: {already_correct}\n"
        f"No receipt     : {no_receipt} (no submitted, non-return Purchase Receipt "
        "with a warehouse)\n"
        f"Ambiguous      : {ambiguous} (receipts name more than one warehouse — left alone)\n\n"
        "The free-text location notes on these Containers are handled by "
        "heal_container_location_notes, which runs next."
    )

    frappe.logger().info(
        f"[MI1-I103] Container accepted warehouse: {healed} healed, "
        f"{already_correct} already correct, {scanned} scanned."
    )
    frappe.log_error(title=LOG_TITLE, message=summary)
    frappe.db.commit()
