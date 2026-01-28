# Set FSC for batches where FSC is empty
# Copy and paste this into System Console

# ============ CONFIGURATION ============
dry_run = True  # Set to False to actually update
new_fsc_value = "."  # Set FSC to "."
# =======================================

# Find batches where custom_fsc is empty or NULL
batches = frappe.db.sql("SELECT name, custom_fsc, custom_container_no FROM `tabBatch` WHERE custom_fsc IS NULL OR custom_fsc = ''", as_dict=1)

print("=" * 60)
print("BATCHES WITH EMPTY FSC")
print("=" * 60)
print("Total found: " + str(len(batches)))
print("=" * 60)

updated = 0
for batch in batches:
    print("Batch: " + batch.name + " | Container: " + str(batch.custom_container_no or ""))

    if not dry_run:
        frappe.db.set_value("Batch", batch.name, "custom_fsc", new_fsc_value)
        updated = updated + 1

if not dry_run:
    frappe.db.commit()
    print("")
    print("=" * 60)
    print("Updated " + str(updated) + " batches - FSC set to '" + new_fsc_value + "'")
    print("=" * 60)

if dry_run:
    print("")
    print("DRY RUN - No changes made")
    print("Set dry_run = False to update batches")
