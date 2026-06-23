# Copyright (c) 2026, reformiqo and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class TransactionType(Document):
    """MI1-I70 (2026-06-23): tiny lookup table for the values that used
    to live as a hardcoded Select on the transaction_type Custom Field
    (VFY / HTY). Promoting the values into their own DocType lets users
    add new transaction types from the desk without a code change.

    Existing 'VFY' / 'HTY' Custom-Field string values map straight onto
    Transaction Type document names (autoname is field:transaction_type_name)
    so the migration is data-loss free."""
    pass
