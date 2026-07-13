# MI1 (Raj 2026-07-13): DN submit throws "Batch has negative stock" for
# batches whose Serial and Batch Bundle inward entry has NULL
# posting_date. erpnext's time-conditioned batch availability query
# uses `CombineDatetime(sbb.posting_date, sbb.posting_time) < DN.posting_date`.
# When posting_date is NULL, that expression evaluates to NULL and the
# row is silently excluded — so available_qty falls to 0 and any
# outward deduction becomes negative-stock.
#
# Local scan on 2026-07-13 found 17,542 submitted SBBs with NULL
# posting_date out of 379k. Each of those blocks stock movement for
# every batch it references.
#
# Heal SQL-copies posting_date + posting_time from the linked voucher
# (Purchase Receipt / Delivery Note / Stock Entry). Only touches SBBs
# whose posting_date is NULL and whose voucher exists — cancelled or
# missing vouchers are skipped.

import frappe


CHUNK_SIZE = 5000


def execute():
    voucher_map = {
        "Purchase Receipt": "Purchase Receipt",
        "Delivery Note": "Delivery Note",
        "Stock Entry": "Stock Entry",
    }

    for voucher_type, tab in voucher_map.items():
        # Find NULL-posting SBBs whose voucher exists.
        candidates = frappe.db.sql(
            f"""
            SELECT sbb.name, v.posting_date, v.posting_time
            FROM `tabSerial and Batch Bundle` sbb
            INNER JOIN `tab{tab}` v ON v.name = sbb.voucher_no
            WHERE sbb.voucher_type = %s
              AND sbb.posting_date IS NULL
              AND v.posting_date IS NOT NULL
            """,
            (voucher_type,),
            as_dict=True,
        )
        if not candidates:
            continue

        total = 0
        for i in range(0, len(candidates), CHUNK_SIZE):
            for row in candidates[i:i + CHUNK_SIZE]:
                update = {"posting_date": row["posting_date"]}
                if row.get("posting_time") is not None:
                    update["posting_time"] = row["posting_time"]
                frappe.db.set_value(
                    "Serial and Batch Bundle", row["name"],
                    update,
                    update_modified=False,
                )
                total += 1
            frappe.db.commit()

        print(f"Healed {total} SBBs for voucher_type={voucher_type}.")
