# Copyright (c) 2026, reformiqo and Contributors
# Tests for calculate_delivery_note_totals validate hook

import frappe
from frappe.tests.utils import FrappeTestCase
from unittest.mock import MagicMock
from mhr.utilis import calculate_delivery_note_totals


class TestCalculateDeliveryNoteTotals(FrappeTestCase):

    def _make_doc(self, items):
        """Build a minimal mock Delivery Note doc with given items."""
        doc = MagicMock()
        doc.items = []
        for cone, qty in items:
            row = MagicMock()
            row.custom_cone = cone
            row.qty = qty
            doc.items.append(row)
        return doc

    def test_totals_calculated_correctly(self):
        doc = self._make_doc([(6, 22.9), (8, 22.8), (6, 45.0)])
        calculate_delivery_note_totals(doc)
        self.assertEqual(doc.custom_total_cone, 20)   # 6+8+6
        self.assertEqual(doc.custom_item_length, 3)

    def test_empty_items(self):
        doc = self._make_doc([])
        calculate_delivery_note_totals(doc)
        self.assertEqual(doc.custom_total_cone, 0)
        self.assertEqual(doc.custom_item_length, 0)

    def test_none_cone_treated_as_zero(self):
        doc = self._make_doc([(None, 10), (8, 5)])
        calculate_delivery_note_totals(doc)
        self.assertEqual(doc.custom_total_cone, 8)
        self.assertEqual(doc.custom_item_length, 2)

    def test_single_item(self):
        doc = self._make_doc([(12, 100.0)])
        calculate_delivery_note_totals(doc)
        self.assertEqual(doc.custom_total_cone, 12)
        self.assertEqual(doc.custom_item_length, 1)

    def test_method_arg_ignored(self):
        """Hook is also called with method='validate' — should work fine."""
        doc = self._make_doc([(4, 10)])
        calculate_delivery_note_totals(doc, method="validate")
        self.assertEqual(doc.custom_total_cone, 4)
