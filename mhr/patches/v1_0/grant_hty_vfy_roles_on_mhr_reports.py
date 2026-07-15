# MI1-I80 (Raj 2026-07-14): grant the HTY User + VFY User roles read
# access to every mhr report. Composes with MI1-I61's data-filter
# enforcement (mhr.utilis.enforce_role_scoped_transaction_type) so:
#
#   * A user with only HTY User → can open every mhr report, but every
#     row is scoped to transaction_type='HTY'.
#   * A user with only VFY User → same but scoped to 'VFY'.
#   * A user with BOTH roles → sees everything (dual-role bypass in
#     enforce_role_scoped_transaction_type).
#   * A user with NEITHER role AND none of the existing role list
#     (Stock User / System Manager / etc.) can't open the reports at
#     all — Frappe blocks it before execute() runs.
#
# Idempotent — only inserts a Has Role row if the (report, role) pair
# isn't already present. Existing roles on each report are preserved.

import frappe


ROLES_TO_GRANT = ("HTY User", "VFY User")


def execute():
    reports = frappe.db.sql(
        "SELECT name FROM `tabReport` WHERE module = 'Mhr'",
        as_dict=True,
    )
    if not reports:
        return

    granted = 0
    for r in reports:
        name = r["name"]
        # Skip if the report doesn't exist as a document.
        if not frappe.db.exists("Report", name):
            continue

        for role in ROLES_TO_GRANT:
            # Guard against the role not being created yet — the
            # create_hty_vfy_roles patch is meant to run first per
            # patches.txt order, but if it hasn't, skip cleanly.
            if not frappe.db.exists("Role", role):
                continue
            already = frappe.db.exists(
                "Has Role",
                {"parent": name, "parenttype": "Report", "role": role},
            )
            if already:
                continue
            doc = frappe.new_doc("Has Role")
            doc.parent = name
            doc.parenttype = "Report"
            doc.parentfield = "roles"
            doc.role = role
            doc.insert(ignore_permissions=True)
            granted += 1

    frappe.db.commit()
    print(f"Granted HTY User / VFY User roles on mhr reports: {granted} new role rows.")
