"""MI1-I39 follow-up: rename Normal → VFY in the transaction_type field.

The Phase-1 backfill patch set every legacy row's transaction_type to
'Normal'. Per Nainsi's new instruction the visible label is being
renamed to 'VFY' (the existing Meher / VFY pipeline). This patch
mirrors that change in three places:

  1) Custom Field on 6 DocTypes — `options` and `default` flip from
     'Normal\\nHTY' → 'VFY\\nHTY' and 'Normal' → 'VFY'.
  2) Every existing row in `tabContainer`, `tabSales Order`,
     `tabDelivery Note`, `tabStock Entry`, `tabPrint Batch`,
     `tabDelivery Trip` that carries transaction_type='Normal' gets
     UPDATEd to 'VFY'.
  3) Client Scripts in module=Mhr whose `script` body still mentions
     the literal string 'Normal' (in comments / fallback branches) get
     a textual replace so the labels stay consistent.

Idempotent — running this twice is a no-op:
  - Custom Field already 'VFY' won't change.
  - 'Normal' rows are 0 the second time around.
  - Client Scripts already containing 'VFY' won't match the LIKE filter.
"""

import frappe


DOCTYPES = [
    "Container",
    "Sales Order",
    "Delivery Note",
    "Stock Entry",
    "Print Batch",
    "Delivery Trip",
]

TABLES = [
    "tabContainer",
    "tabSales Order",
    "tabDelivery Note",
    "tabStock Entry",
    "tabPrint Batch",
    "tabDelivery Trip",
]


def execute():
    _rename_custom_field_options()
    _migrate_existing_rows()
    _rename_in_client_scripts()
    frappe.db.commit()


def _rename_custom_field_options():
    for dt in DOCTYPES:
        cf_name = frappe.db.get_value(
            "Custom Field",
            {"dt": dt, "fieldname": "transaction_type"},
            "name",
        )
        if not cf_name:
            frappe.logger().info(
                f"[MI1-I39 rename] {dt}: no transaction_type Custom Field — skipping."
            )
            continue
        # MI1-I70 later converts this Select(VFY|HTY) -> Link(Transaction Type).
        # On a fresh install the field is created as a Link straight from the
        # fixture, so DON'T rewrite Select options onto a Link field — that
        # leaves Link + options 'VFY\nHTY' and breaks every transaction_type
        # link. Only touch it while it's still a Select.
        if frappe.db.get_value("Custom Field", cf_name, "fieldtype") != "Select":
            continue
        frappe.db.set_value("Custom Field", cf_name, {
            "options": "VFY\nHTY",
            "default": "VFY",
        })


def _migrate_existing_rows():
    for tbl in TABLES:
        if not _column_exists(tbl, "transaction_type"):
            continue
        frappe.db.sql(
            f"UPDATE `{tbl}` SET transaction_type='VFY' WHERE transaction_type='Normal'"
        )


def _rename_in_client_scripts():
    scripts = frappe.db.sql(
        """
        SELECT name, script
        FROM `tabClient Script`
        WHERE module = 'Mhr' AND script LIKE %s
        """,
        ("%Normal%",),
        as_dict=True,
    )
    for s in scripts:
        new_script = s.script.replace("Normal", "VFY")
        frappe.db.set_value("Client Script", s.name, "script", new_script)


def _column_exists(table, column):
    rows = frappe.db.sql(
        """
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = DATABASE()
          AND table_name = %s
          AND column_name = %s
        LIMIT 1
        """,
        (table, column),
    )
    return bool(rows)
