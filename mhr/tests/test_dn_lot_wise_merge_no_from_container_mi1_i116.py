"""MI1-I116 (Raj 2026-08-31) — Merge No must be container-wise, never the note's.

The DN report was fixed in 0b370d3 (per (container, lot) from the Container
master). Delivery Note Lot-Wise still selected dn.custom_merge_no — the
note-level aggregate that shows the first container's value on every lot —
so it now runs through the same resolver.
"""
import inspect
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from mhr.mhr.report.dn import dn as dn_report
from mhr.mhr.report.delivery_note_lot_wise import delivery_note_lot_wise as lot_wise


class TestLotWiseNoLongerReadsTheHeader(FrappeTestCase):

    def test_header_field_is_gone_from_the_query(self):
        src = inspect.getsource(lot_wise.get_data)
        self.assertNotIn("dn.custom_merge_no", src, "Raj: never from the parent Delivery Note field.")
        self.assertIn("_merge_numbers_by_container_and_lot(rows)", src)
        self.assertIn('row["merge_no"] = merge_numbers.get(_container_lot_key(row)) or ""', src)

    def test_column_still_there(self):
        self.assertIn("merge_no", [c["fieldname"] for c in lot_wise.get_columns()])


class TestResolverAcceptsLotWiseRows(FrappeTestCase):
    """Lot-Wise rows spell the column `container_no`; the DN report's rows spell
    it `container`. One resolver serves both."""

    CONTAINERS = [
        {"container_no": "MCJC-1111", "lot_no": "01012001", "merge_no": "TRYL"},
        {"container_no": "MCJC-2222", "lot_no": "13042026", "merge_no": "ABCD"},
    ]

    def _resolve(self, rows):
        with patch.object(dn_report.frappe, "get_all", return_value=[frappe._dict(c) for c in self.CONTAINERS]) as get_all:
            merge = dn_report._merge_numbers_by_container_and_lot(rows)
            asked = sorted(get_all.call_args.kwargs["filters"]["container_no"][1])
        return [merge.get(dn_report._container_lot_key(r)) or "" for r in rows], asked

    def test_raj_example_each_container_gets_its_own_merge_no(self):
        rows = [frappe._dict(container_no="MCJC-1111", lot_no="01012001"),
                frappe._dict(container_no="MCJC-2222", lot_no="13042026")]
        resolved, asked = self._resolve(rows)
        self.assertEqual(resolved, ["TRYL", "ABCD"])
        self.assertEqual(asked, ["MCJC-1111", "MCJC-2222"], "container_no-keyed rows must drive the lookup.")

    def test_dn_report_rows_still_work(self):
        rows = [frappe._dict(container="MCJC-2222", lot_no="13042026")]
        self.assertEqual(self._resolve(rows)[0], ["ABCD"])


class TestLotWiseOnRealData(FrappeTestCase):
    """Every Lot-Wise row's Merge No equals what the Container master holds
    for that row's own (container, lot) — not what the note header says."""

    def test_rows_match_the_container_master(self):
        dn = frappe.db.sql("""
            SELECT dni.parent FROM `tabDelivery Note Item` dni
            JOIN `tabDelivery Note` dn ON dn.name = dni.parent
            WHERE dn.docstatus = 1 AND IFNULL(dn.transaction_type, 'VFY') = 'VFY'
              AND dn.posting_date >= '2026-05-01'
            GROUP BY dni.parent HAVING COUNT(DISTINCT dni.custom_container_no) >= 2
            ORDER BY dn.posting_date DESC LIMIT 1""")
        if not dn:
            self.skipTest("No recent multi-container VFY note on this bench.")
        name = dn[0][0]
        _, rows = lot_wise.execute(frappe._dict(delivery_note=name))
        self.assertGreaterEqual(len(rows), 2)
        for r in rows:
            expected = sorted({m for m in frappe.get_all(
                "Container",
                filters={"container_no": r["container_no"], "lot_no": r["lot_no"], "docstatus": ("<", 2)},
                pluck="merge_no") if m})
            self.assertEqual(r["merge_no"], ", ".join(expected),
                             f"{name} {r['container_no']}/{r['lot_no']}: report says {r['merge_no']!r}, master says {expected}")
