# MI1-I70 (2026-06-23): convert all `transaction_type` Custom Fields from
# Select(VFY|HTY) → Link(Transaction Type).
#
# Steps:
#   1. Reload the new Transaction Type doctype from disk (created in this
#      same release under mhr/mhr/doctype/transaction_type/).
#   2. Seed the two existing values (VFY, HTY) as Transaction Type docs
#      so the Link target table is populated.
#   3. Backfill — add a row for every DISTINCT transaction_type value that
#      currently appears on tabContainer (covers any legacy values beyond
#      VFY/HTY without losing data).
#   4. Flip each of the 6 Custom Fields:
#        fieldtype: Select  -> Link
#        options:   "VFY\nHTY"  -> "Transaction Type"
#
# Idempotent — safe to re-run.

import frappe


SEED_VALUES = ("VFY", "HTY")

CUSTOM_FIELDS_TO_CONVERT = (
    "Container-transaction_type",
    "Delivery Note-transaction_type",
    "Delivery Trip-transaction_type",
    "Print Batch-transaction_type",
    "Sales Order-transaction_type",
    "Stock Entry-transaction_type",
)


def execute():
    # Step 1: reload the new doctype from disk (no-op if already present).
    frappe.reload_doc("mhr", "doctype", "transaction_type", force=True)

    # Step 2 + 3: seed VFY, HTY + any distinct legacy value on Container.
    legacy_values = set(SEED_VALUES)
    # try/except because some sites may not have the Container table yet
    # (fresh installs running this patch before any data lands).
    try:
        rows = frappe.db.sql(
            "SELECT DISTINCT transaction_type FROM `tabContainer` "
            "WHERE transaction_type IS NOT NULL AND transaction_type != ''"
        )
        legacy_values.update({r[0] for r in rows if r[0]})
    except Exception:
        pass

    for name in sorted(legacy_values):
        if not frappe.db.exists("Transaction Type", name):
            doc = frappe.new_doc("Transaction Type")
            doc.transaction_type_name = name
            doc.insert(ignore_permissions=True)

    # Step 4: flip each Custom Field's fieldtype + options.
    for cf_name in CUSTOM_FIELDS_TO_CONVERT:
        if not frappe.db.exists("Custom Field", cf_name):
            # The field hasn't been deployed to this site yet — skip
            # silently; the fixture import on the next migrate creates it
            # with the right values.
            continue
        frappe.db.set_value(
            "Custom Field", cf_name,
            {
                "fieldtype": "Link",
                "options": "Transaction Type",
            },
            update_modified=False,
        )

    frappe.db.commit()
    # Clear doctype metadata cache so the new fieldtype takes effect on
    # the next form load.
    frappe.clear_cache(doctype="Container")
    frappe.clear_cache(doctype="Delivery Note")
    frappe.clear_cache(doctype="Delivery Trip")
    frappe.clear_cache(doctype="Print Batch")
    frappe.clear_cache(doctype="Sales Order")
    frappe.clear_cache(doctype="Stock Entry")
