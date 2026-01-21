# Copyright (c) 2026, reformiqo and contributors
# Migration script to create Item Specification records and update existing data

import frappe


def execute():
    """
    Migrate existing Select field values to Item Specification Link fields.
    This patch runs after the Item Specification doctype is created.
    """

    # Check if Item Specification doctype exists
    if not frappe.db.exists("DocType", "Item Specification"):
        print("Item Specification doctype does not exist. Skipping migration.")
        return

    # Field mappings: (doctype, fieldname, specification_type)
    field_mappings = [
        # Container
        ("Container", "glue", "Glue"),
        ("Container", "lusture", "Lusture"),
        ("Container", "grade", "Grade"),
        ("Container", "pulp", "Pulp"),
        ("Container", "fsc", "FSC"),
        ("Container", "cross_section", "Cross Section"),
        # Batch
        ("Batch", "custom_glue", "Glue"),
        ("Batch", "custom_lusture", "Lusture"),
        ("Batch", "custom_grade", "Grade"),
        ("Batch", "custom_pulp", "Pulp"),
        ("Batch", "custom_fsc", "FSC"),
        ("Batch", "custom_cross_section", "Cross Section"),
        # Delivery Note
        ("Delivery Note", "custom_glue", "Glue"),
        ("Delivery Note", "custom_lusture", "Lusture"),
        ("Delivery Note", "custom_grade", "Grade"),
        ("Delivery Note", "custom_pulp", "Pulp"),
        ("Delivery Note", "custom_fsc", "FSC"),
        # Purchase Receipt
        ("Purchase Receipt", "custom_glue", "Glue"),
        ("Purchase Receipt", "custom_lusture", "Lusture"),
        ("Purchase Receipt", "custom_grade", "Grade"),
        ("Purchase Receipt", "custom_pulp", "Pulp"),
        ("Purchase Receipt", "custom_fsc", "FSC"),
    ]

    created_specs = set()
    updated_records = 0

    for doctype, fieldname, spec_type in field_mappings:
        print(f"\nProcessing {doctype}.{fieldname} ({spec_type})...")

        # Check if field exists in doctype
        if not frappe.db.has_column(doctype, fieldname):
            print(f"  Skipping - field {fieldname} does not exist in {doctype}")
            continue

        # Get all unique values from this field
        try:
            values = frappe.db.sql(f"""
                SELECT DISTINCT `{fieldname}` as value
                FROM `tab{doctype}`
                WHERE `{fieldname}` IS NOT NULL
                AND `{fieldname}` != ''
                AND `{fieldname}` != '.'
                AND `{fieldname}` NOT LIKE '%-%'
            """, as_dict=True)
        except Exception as e:
            print(f"  Skipping - error reading field: {e}")
            continue

        for row in values:
            old_value = row.value
            if not old_value or old_value == '.' or old_value == 'NULL':
                continue

            # New document name format
            new_name = f"{spec_type}-{old_value}"

            # Create Item Specification if not exists
            if new_name not in created_specs:
                if not frappe.db.exists("Item Specification", new_name):
                    try:
                        doc = frappe.get_doc({
                            "doctype": "Item Specification",
                            "specification_type": spec_type,
                            "value": old_value
                        })
                        doc.insert(ignore_permissions=True)
                        print(f"  Created: {new_name}")
                        created_specs.add(new_name)
                    except frappe.DuplicateEntryError:
                        print(f"  Already exists: {new_name}")
                        created_specs.add(new_name)
                    except Exception as e:
                        print(f"  Error creating {new_name}: {e}")
                else:
                    created_specs.add(new_name)

            # Update records to use new naming
            try:
                frappe.db.sql(f"""
                    UPDATE `tab{doctype}`
                    SET `{fieldname}` = %s
                    WHERE `{fieldname}` = %s
                """, (new_name, old_value))
                updated = frappe.db.sql("SELECT ROW_COUNT()")[0][0]
                if updated > 0:
                    print(f"  Updated {updated} records: '{old_value}' -> '{new_name}'")
                    updated_records += updated
            except Exception as e:
                print(f"  Error updating records: {e}")

    frappe.db.commit()
    print(f"\n{'='*50}")
    print(f"Migration complete!")
    print(f"Item Specifications created: {len(created_specs)}")
    print(f"Records updated: {updated_records}")
