# Copyright (c) 2026, reformiqo and contributors
# For license information, please see license.txt
#
# MI1-I50 P4 — show Receive entries linked back to a Send entry inside the
# standard Connections panel on the Send entry's form.
#
# Wired via hooks.py:
#     override_doctype_dashboards = {
#         "Stock Entry": "mhr.overrides.stock_entry_dashboard.get_dashboard_data"
#     }
#
# Frappe calls this AFTER the doctype's own get_dashboard_data builds the
# baseline (Work Order / Purchase Receipt / etc. sections); we append a
# "Subcontract" section whose only item is "Stock Entry" itself — the link
# field on the Receive entry is our custom_original_send_entry. The default
# resolution would look for a field named "stock_entry"; non_standard_fieldnames
# tells Frappe to use ours instead.

from frappe import _


def get_dashboard_data(data):
    """Append a 'Subcontract → Stock Entry' section to the Stock Entry
    Connections panel keyed on custom_original_send_entry."""
    # Frappe sometimes passes an empty dict {} which is FALSY — using
    # `data or {}` would orphan it (mutations would go to a fresh dict,
    # not the caller's). Only replace when None.
    if data is None:
        data = {}

    transactions = data.setdefault("transactions", [])
    # Idempotent — bench restart can replay hooks on the same dict cache.
    if not any(t.get("label") == _("Subcontract") for t in transactions):
        transactions.append({
            "label": _("Subcontract"),
            "items": ["Stock Entry"],
        })

    non_standard = data.setdefault("non_standard_fieldnames", {})
    non_standard["Stock Entry"] = "custom_original_send_entry"

    return data
