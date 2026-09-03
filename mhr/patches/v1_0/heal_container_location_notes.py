"""MI1-I103 follow-up — restore the free-text Location notes.

heal_container_accepted_warehouse repaired Container.set_warehouse, which had a
definitive source in the inward Purchase Receipt. The same hooks also overwrote
the free-text notes beside it, and those have no such source:

    Container.warehouse       Stock Sheet (Balance Report) "Location" column,
                              and Meher Creation's own `warehouse` column.
    Batch.custom_warehouse    labelled "Location"; Stock Sheet (Balance Report
                              Simple) reads it.
    Batch Items.warehouse     nothing reads it.

Recovering the original
-----------------------
container.py gives every Batch of a container the SAME custom_warehouse at
inward, and the hook only ever replaced it with a Warehouse record's name --
per batch, and only for batches that were actually moved. So the batches that
were never in a Stock Entry still hold the original, and it is the one distinct
value among them that is NOT a warehouse name. Zero or several means it is
gone, and the container is left alone.

On MCJC-2222 the surviving batches hold '', so the original was empty -- which
matches the Container form before MAT-GD-2026-00008 was submitted.

What it does NOT restore
------------------------
Container.warehouse on HTY containers. resolved_specs() feeds Batch.custom_warehouse
from Container.location in HTY mode and from Container.warehouse in VFY mode, so
on HTY the batches say nothing about what `warehouse` held. Copying location
across would invent data rather than restore it.

Batch Items.warehouse at all. The hook updated every row of the container in one
statement (`WHERE parent = %s`), so no row kept the original. Nothing reads the
field either.

Idempotent: a second run finds nothing left holding a warehouse name.
"""

import frappe

BATCH_SIZE = 500
LOG_TITLE = "MI1-I103 Container location-note heal"

HTY = "HTY"


def execute():
    candidates = _damaged_containers()
    if not candidates:
        _report(0, 0, 0, 0, 0)
        return

    warehouse_names = set(frappe.get_all("Warehouse", pluck="name"))
    by_container = _batch_locations([row["name"] for row in candidates])

    containers_healed = batches_healed = unrecoverable = hty_skipped = 0
    pending = 0

    for row in candidates:
        rows = by_container.get(row["name"]) or []
        original = _inward_location(rows, warehouse_names)
        if original is None:
            unrecoverable += 1
            continue

        for batch in rows:
            if batch["location"] in warehouse_names:
                frappe.db.set_value(
                    "Batch",
                    batch["batch"],
                    "custom_warehouse",
                    original,
                    update_modified=False,
                )
                batches_healed += 1
                pending += 1
                if pending >= BATCH_SIZE:
                    frappe.db.commit()
                    pending = 0

        if (row["transaction_type"] or "").upper() == HTY:
            hty_skipped += 1
            continue

        frappe.db.set_value(
            "Container", row["name"], "warehouse", original, update_modified=False
        )
        containers_healed += 1
        pending += 1

    if pending:
        frappe.db.commit()

    _report(
        len(candidates), containers_healed, batches_healed, unrecoverable, hty_skipped
    )


def _damaged_containers():
    """Submitted Containers whose Warehouse note is a real Warehouse name."""
    return frappe.db.sql(
        """
        SELECT c.name, c.warehouse, c.transaction_type
        FROM `tabContainer` c
        INNER JOIN `tabWarehouse` w ON w.name = c.warehouse
        WHERE c.docstatus = 1
        """,
        as_dict=True,
    )


def _batch_locations(container_names):
    """Container docname -> [{batch, location}] via its own Batch Items rows.

    Joined through the child table rather than the container-number column on Batch,
    because several Container documents share one container_no (one per lot).
    """
    if not container_names:
        return {}

    placeholders = ", ".join(["%s"] * len(container_names))
    rows = frappe.db.sql(
        f"""
        SELECT bi.parent AS container, b.name AS batch,
               IFNULL(b.custom_warehouse, '') AS location
        FROM `tabBatch Items` bi
        INNER JOIN `tabBatch` b ON b.name = bi.batch_id
        WHERE bi.parenttype = 'Container'
          AND bi.parent IN ({placeholders})
        """,
        tuple(container_names),
        as_dict=True,
    )

    grouped = {}
    for row in rows:
        grouped.setdefault(row["container"], []).append(row)
    return grouped


def _inward_location(rows, warehouse_names):
    """The one value the hook cannot have written, or None if it is gone."""
    survivors = {r["location"] for r in rows} - warehouse_names
    return next(iter(survivors)) if len(survivors) == 1 else None


def _report(scanned, containers_healed, batches_healed, unrecoverable, hty_skipped):
    summary = (
        f"Scanned            : {scanned} submitted Containers whose Warehouse note "
        "holds a real Warehouse name\n"
        f"Containers healed  : {containers_healed} (Container.warehouse restored)\n"
        f"Batches healed     : {batches_healed} (Batch.custom_warehouse restored)\n"
        f"Unrecoverable      : {unrecoverable} (no surviving batch holds the original)\n"
        f"HTY, header skipped: {hty_skipped} (their batches mirror Container.location, "
        "not Container.warehouse — their Batches were still healed)\n\n"
        "Batch Items.warehouse is not restored: the hook rewrote every row of the "
        "container in one statement, so no row kept the original. Nothing reads it."
    )

    frappe.logger().info(
        f"[MI1-I103] location notes: {containers_healed} containers, "
        f"{batches_healed} batches healed, {scanned} scanned."
    )
    frappe.log_error(title=LOG_TITLE, message=summary)
    frappe.db.commit()
