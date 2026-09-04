# Copyright (c) 2026, reformiqo and contributors
# For license information, please see license.txt
#
# MI1-I69 (2026-06-23): converted DN from a Query Report to a Script
# Report so column labels can swap dynamically with the Transaction Type
# filter — Pulp ⇄ Type, Glue ⇄ Product, Lusture ⇄ Colour. The previous
# Query Report flavour declared columns via SQL aliases; rebinding
# labels in JS post-render didn't reliably re-draw the datatable
# headers. Script reports build their `columns` dict per-call, so the
# label swap is honoured every refresh.

import frappe
from frappe import _


def execute(filters=None):
    filters = filters or {}
    # MI1-I61 (Raj 2026-06-27): scope by 'HTY User' / 'VFY User' role.
    from mhr.utilis import enforce_role_scoped_transaction_type
    filters = enforce_role_scoped_transaction_type(filters)
    columns = get_columns(filters)
    data = get_data(filters)
    return columns, data


def _so_progress_columns():
    """MI1-I120: Sales Order / SO Total / SO Delivered / SO Remaining.
    Lazy import — mhr.utilis is imported lazily throughout the reports."""
    from mhr.utilis import SO_PROGRESS_COLUMNS
    return [dict(c) for c in SO_PROGRESS_COLUMNS]


def get_columns(filters):
    # MI1-I64 reopen (Raj 2026-06-29): drop Merge No in HTY (same rule
    # as Balance Report — Merge No is a VFY-only concept).
    is_hty = (filters.get("transaction_type") == "HTY")
    pulp_label = _("Type") if is_hty else _("Pulp")
    glue_label = _("Product") if is_hty else _("Glue")
    lusture_label = _("Colour") if is_hty else _("Lusture")
    columns = [
        {"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 110},
        {"label": _("ID"), "fieldname": "id", "fieldtype": "Data", "width": 180},
        {"label": _("Challan"), "fieldname": "challan", "fieldtype": "Data", "width": 110},
        {"label": _("Date"), "fieldname": "date", "fieldtype": "Date", "width": 100},
        {"label": _("Denier"), "fieldname": "denier", "fieldtype": "Data", "width": 110},
        {"label": pulp_label, "fieldname": "pulp", "fieldtype": "Data", "width": 90},
        {"label": glue_label, "fieldname": "glue", "fieldtype": "Data", "width": 90},
        {"label": lusture_label, "fieldname": "lusture", "fieldtype": "Data", "width": 90},
        {"label": _("Grade"), "fieldname": "grade", "fieldtype": "Data", "width": 80},
        {"label": _("Total Qty"), "fieldname": "total_qty", "fieldtype": "Float", "width": 100, "precision": 3},
    ]
    if not is_hty:
        columns.append({"label": _("Merge No"), "fieldname": "merge_no", "fieldtype": "Data", "width": 90})
    columns += [
        {"label": _("Lot No"), "fieldname": "lot_no", "fieldtype": "Data", "width": 110},
        # Item Length is varchar on Batch (custom_total_item_length) —
        # was Int when sourced from COUNT(dni.name); now per-row from
        # Batch master so the fieldtype follows the source column.
        {"label": _("Item Length"), "fieldname": "item_length", "fieldtype": "Data", "width": 100},
        {"label": _("Container"), "fieldname": "container", "fieldtype": "Data", "width": 120},
        {"label": _("Customer Name"), "fieldname": "customer_name", "fieldtype": "Data", "width": 180},
        # MI1-I120 (Raj 2026-09-02): SO No / SO Total / SO Delivered / SO Remaining.
        *_so_progress_columns(),
        {"label": _("Vehicle No"), "fieldname": "vehicle_no", "fieldtype": "Data", "width": 110},
        {"label": _("Sales Person"), "fieldname": "sales_person", "fieldtype": "Data", "width": 120},
        {"label": _("Total Cone"), "fieldname": "total_cone", "fieldtype": "Float", "width": 100, "precision": 0},
        {"label": _("Supplier Batch No"), "fieldname": "supplier_batch_no", "fieldtype": "Data", "width": 200},
        {"label": _("Driver Name"), "fieldname": "driver_name", "fieldtype": "Data", "width": 140},
        {"label": _("Remark"), "fieldname": "remark", "fieldtype": "Small Text", "width": 200},
    ]
    return columns


def get_data(filters):
    from_date = filters.get("from_date")
    to_date = filters.get("to_date")
    transaction_type = (filters.get("transaction_type") or "").strip()
    if not (from_date and to_date):
        return []

    tt_clause = ""
    if transaction_type in ("VFY", "HTY"):
        # Same EXISTS pattern as before: filter rows whose DN-item's
        # container_no has at least one Container doc with that
        # transaction_type. Avoids row-multiplication from many Container
        # docs sharing one container_no.
        tt_clause = """
            AND EXISTS (
                SELECT 1 FROM `tabContainer` c
                WHERE c.container_no = dni.custom_container_no
                  AND c.transaction_type = %(transaction_type)s
            )
        """

    rows = frappe.db.sql(
        f"""
        SELECT
            CASE
              WHEN dn.status = 'Completed' THEN CONCAT('<span style="color:green;">', dn.status, '</span>')
              WHEN dn.status = 'To Bill' THEN CONCAT('<span style="color:orange;">', dn.status, '</span>')
              WHEN dn.status = 'To Deliver and Bill' THEN CONCAT('<span style="color:blue;">', dn.status, '</span>')
              WHEN dn.status = 'Draft' THEN CONCAT('<span style="color:gray;">', dn.status, '</span>')
              WHEN dn.status = 'Cancelled' THEN CONCAT('<span style="color:red;">', dn.status, '</span>')
              WHEN dn.status = 'Closed' THEN CONCAT('<span style="color:purple;">', dn.status, '</span>')
              ELSE dn.status
            END AS `status`,
            CONCAT('<a href="/app/delivery-note/', dn.name, '">', dn.name, '</a>') AS `id`,
            dn.challan_number AS `challan`,
            dn.posting_date AS `date`,
            -- MI1-I64 follow-up (2026-06-24): denier comes from the Batch
            -- master so it always matches the batch's actual item
            -- (b.item is canonical; dni.item_code is the DN row's copy).
            MAX(b.item) AS `denier`,
            -- Batch attributes (Pulp / Glue / Lusture / Grade) MUST be
            -- per-row from the linked Batch — NOT from the DN header.
            -- Previously the SQL had COALESCE(NULLIF(dn.custom_*, ''),
            -- b.custom_*) which preferred the DN-level aggregated value
            -- (set by set_header_container_info_from_items as
            -- comma-joined or first-of-distinct). When a Sample Challan
            -- had multiple batches with different attributes every row
            -- showed the same (aggregated) header value. MAX() picks the
            -- single batch value within the per-row GROUP BY scope.
            SUBSTRING_INDEX(MAX(b.custom_pulp), '-', -1) AS `pulp`,
            SUBSTRING_INDEX(MAX(b.custom_glue), '-', -1) AS `glue`,
            SUBSTRING_INDEX(MAX(b.custom_lusture), '-', -1) AS `lusture`,
            SUBSTRING_INDEX(MAX(b.custom_grade), '-', -1) AS `grade`,
            SUM(dni.qty) AS `total_qty`,
            -- Merge No is filled in afterwards from the Container master,
            -- keyed on this row's container AND lot.
            -- See _merge_numbers_by_container_and_lot.
            dni.custom_lot_no AS `lot_no`,
            -- Item Length: prefer the Batch master's
            -- custom_total_item_length when populated; fall back to
            -- COUNT(dni.name) (the DN-row count within the per-row
            -- GROUP BY scope) for batches that have no length stored.
            -- COUNT is cast to CHAR so both branches share a varchar
            -- column type.
            COALESCE(
                NULLIF(MAX(b.custom_total_item_length), ''),
                CAST(COUNT(dni.name) AS CHAR)
            ) AS `item_length`,
            dni.custom_container_no AS `container`,
            dn.customer_name AS `customer_name`,
            -- MI1-I120: the order this note delivers against — the header
            -- link, else the per-row link the SO -> DN mapper writes.
            COALESCE(NULLIF(dn.custom_sales_order, ''), MAX(dni.against_sales_order)) AS `sales_order`,
            dn.vehicle_no AS `vehicle_no`,
            dn.custom_sales_person AS `sales_person`,
            SUM(dni.custom_cone) AS `total_cone`,
            GROUP_CONCAT(DISTINCT dni.custom_supplier_batch_no SEPARATOR ', ') AS `supplier_batch_no`,
            dn.driver_name AS `driver_name`,
            MAX(dn.remark) AS `remark`
        FROM `tabDelivery Note` dn
        LEFT JOIN `tabDelivery Note Item` dni ON dni.parent = dn.name
        LEFT JOIN `tabBatch` b ON b.name = dni.batch_no
        WHERE
            dn.docstatus < 2
            AND dn.posting_date BETWEEN %(from_date)s AND %(to_date)s
            {tt_clause}
        GROUP BY
            dn.name,
            dni.item_code,
            dni.custom_container_no,
            dni.custom_lot_no
        ORDER BY dn.posting_date DESC, dn.name, dni.item_code
        """,
        {
            "from_date": from_date,
            "to_date": to_date,
            "transaction_type": transaction_type,
        },
        as_dict=True,
    )

    # Merge No is VFY-only (see get_columns), so skip the lookup entirely on
    # HTY where the column is not rendered.
    if transaction_type != "HTY":
        merge_numbers = _merge_numbers_by_container_and_lot(rows)
        for row in rows:
            row["merge_no"] = merge_numbers.get(_container_lot_key(row)) or ""

    # MI1-I120: SO Total / Delivered / Remaining per Sales Order; rows
    # without one stay blank.
    from mhr.utilis import annotate_so_progress
    return annotate_so_progress(rows)


def _container_lot_key(row):
    """(container_no, lot_no), normalised the same way on both sides."""
    return (
        (row.get("container_no") or row.get("container") or "").strip(),
        (row.get("lot_no") or "").strip(),
    )


def _merge_numbers_by_container_and_lot(rows):
    """(container_no, lot_no) -> the Merge No the Container master holds.

    This used to come off the Delivery Note header
    (dn.custom_merge_no), which set_header_container_info_from_items fills by
    aggregating the note's rows. Being per-note, it showed the same value on
    every row of the note whatever that row's container was — the same fault
    the Pulp / Glue / Lusture / Grade columns above were already fixed for.
    Merge No belongs to the container, so it is read from the Container master.

    Keyed on the LOT as well as the container, because a container_no is not
    unique: it carries one Container document per lot. MCJC-1111 holds H30X
    against lot 6032025 and TRYL against lot 01012001, so a report row for lot
    01012001 must read TRYL alone — keying on the container by itself put both
    on every row of that container. The report already groups by
    dni.custom_lot_no, so the pair is exactly this row's own identity.

    Values are still comma-joined for the case where two Container documents
    share one container and lot, the way supplier_batch_no is joined.

    One query for the whole report rather than a join or a correlated
    subquery — several Container documents share a container_no, and joining
    on it multiplies the report's rows. That is the same reason the
    transaction_type filter above uses EXISTS.
    """
    containers = sorted({(row.get("container") or "").strip() for row in rows} - {""})
    if not containers:
        return {}

    container_rows = frappe.get_all(
        "Container",
        # Cancelled containers are not part of the stock story any more, and
        # their merge numbers should not surface against a delivery.
        filters={"container_no": ("in", containers), "docstatus": ("<", 2)},
        fields=["container_no", "lot_no", "merge_no"],
    )

    by_key = {}
    for container in container_rows:
        merge_no = (container.get("merge_no") or "").strip()
        if merge_no:
            by_key.setdefault(_container_lot_key(container), set()).add(merge_no)

    return {key: ", ".join(sorted(merge_numbers)) for key, merge_numbers in by_key.items()}
