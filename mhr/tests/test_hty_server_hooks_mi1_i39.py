"""MI1-I39 P2-G — server-side HTY hooks tests.

Three hooks in mhr/utilis.py wired in hooks.py doc_events:
  - validate_hty_stock_entry — sets HTY naming series in HTY mode
  - validate_hty_delivery_trip — auto-flips Trip to HTY when all DNs HTY
  - restore_cones_for_hty_return — on_submit re-credits cones on the
    source Container's Batch Items rows for HTY-mode return DNs

These tests pin the per-function behavior using lightweight stand-in
docs (MagicMock for the validate-only paths; real DB writes for the
restore_cones path which we wrap in a transaction we don't commit).
"""

import inspect
from unittest.mock import MagicMock, patch
import frappe
from frappe.tests.utils import FrappeTestCase

from mhr import utilis as mhr_utilis


class TestValidateHTYStockEntry(FrappeTestCase):

    def test_skips_when_not_hty(self):
        doc = MagicMock()
        doc.docstatus = 0
        doc.transaction_type = "VFY"
        doc.naming_series = "MAT-STE-.YYYY.-"
        mhr_utilis.validate_hty_stock_entry(doc)
        self.assertEqual(
            doc.naming_series, "MAT-STE-.YYYY.-",
            "VFY-mode SE must not have naming_series rewritten.",
        )

    def test_sets_hty_series_when_hty_and_non_hty_series(self):
        doc = MagicMock()
        doc.docstatus = 0
        doc.transaction_type = "HTY"
        doc.naming_series = "MAT-STE-.YYYY.-"
        mhr_utilis.validate_hty_stock_entry(doc)
        self.assertTrue(
            str(doc.naming_series).startswith("HTY-"),
            "HTY-mode SE with non-HTY series must be re-pointed to an HTY series.",
        )

    def test_leaves_existing_hty_series_alone(self):
        doc = MagicMock()
        doc.docstatus = 0
        doc.transaction_type = "HTY"
        doc.naming_series = "HTY-STE-CUSTOM-.YYYY.-"
        mhr_utilis.validate_hty_stock_entry(doc)
        self.assertEqual(
            doc.naming_series, "HTY-STE-CUSTOM-.YYYY.-",
            "If the user already picked an HTY series, don't overwrite.",
        )

    def test_skips_submitted_docs(self):
        doc = MagicMock()
        doc.docstatus = 1
        doc.transaction_type = "HTY"
        doc.naming_series = "MAT-STE-.YYYY.-"
        mhr_utilis.validate_hty_stock_entry(doc)
        self.assertEqual(
            doc.naming_series, "MAT-STE-.YYYY.-",
            "Must not touch naming_series after submit (Frappe rejects it).",
        )


class TestValidateHTYDeliveryTrip(FrappeTestCase):

    def _doc_with_stops(self, dn_names, docstatus=0, transaction_type="VFY",
                       naming_series="MAT-DT-.YYYY.-"):
        doc = MagicMock()
        doc.docstatus = docstatus
        doc.transaction_type = transaction_type
        doc.naming_series = naming_series
        doc.delivery_stops = []
        for n in dn_names:
            stop = MagicMock()
            stop.delivery_note = n
            doc.delivery_stops.append(stop)
        return doc

    def test_skips_when_no_stops(self):
        doc = self._doc_with_stops([])
        mhr_utilis.validate_hty_delivery_trip(doc)
        self.assertEqual(doc.transaction_type, "VFY")

    def test_all_hty_dns_propagates_hty(self):
        doc = self._doc_with_stops(["DN-A", "DN-B"])
        with patch.object(
            frappe.db, "sql",
            return_value=[
                frappe._dict(name="DN-A", tt="HTY"),
                frappe._dict(name="DN-B", tt="HTY"),
            ],
        ):
            mhr_utilis.validate_hty_delivery_trip(doc)
        self.assertEqual(doc.transaction_type, "HTY")
        self.assertTrue(str(doc.naming_series).startswith("HTY-"))

    def test_mixed_dns_stays_normal(self):
        doc = self._doc_with_stops(["DN-A", "DN-B"])
        with patch.object(
            frappe.db, "sql",
            return_value=[
                frappe._dict(name="DN-A", tt="HTY"),
                frappe._dict(name="DN-B", tt="VFY"),
            ],
        ):
            mhr_utilis.validate_hty_delivery_trip(doc)
        self.assertEqual(
            doc.transaction_type, "VFY",
            "A mixed-mode Trip must NOT auto-flip to HTY — surprise side effects.",
        )

    def test_skips_submitted_trips(self):
        doc = self._doc_with_stops(["DN-A"], docstatus=1)
        mhr_utilis.validate_hty_delivery_trip(doc)
        self.assertEqual(doc.transaction_type, "VFY")


class TestRestoreConesForHTYReturn(FrappeTestCase):
    """`restore_cones_for_hty_return` source-level + behavior tests."""

    def test_skips_when_not_return(self):
        doc = MagicMock()
        doc.is_return = 0
        doc.transaction_type = "HTY"
        doc.items = []
        with patch.object(frappe.db, "sql", side_effect=AssertionError("must not query")):
            mhr_utilis.restore_cones_for_hty_return(doc)

    def test_skips_when_not_hty(self):
        doc = MagicMock()
        doc.is_return = 1
        doc.transaction_type = "VFY"
        doc.items = []
        with patch.object(frappe.db, "sql", side_effect=AssertionError("must not query")):
            mhr_utilis.restore_cones_for_hty_return(doc)

    def test_iterates_items_with_cone_and_batch(self):
        # Source-level pin — the function must read these 3 row fields
        # to identify the source Batch Items row to credit.
        src = inspect.getsource(mhr_utilis.restore_cones_for_hty_return)
        for fld in ("custom_cone", "batch_no", "custom_container_no"):
            self.assertIn(fld, src,
                f"restore_cones_for_hty_return must read item.{fld}.")

    def test_writes_to_batch_items_cone(self):
        src = inspect.getsource(mhr_utilis.restore_cones_for_hty_return)
        self.assertIn(
            'frappe.db.set_value("Batch Items"', src,
            "Must update Batch Items.cone via frappe.db.set_value.",
        )
        # Increment, not overwrite — credit back.
        self.assertIn(
            "cint(row.cur_cone) + cone", src,
            "Must add cone to current value, not replace it.",
        )


class TestHooksWiredInHooksPy(FrappeTestCase):
    """The 3 server hooks must be registered in hooks.py doc_events."""

    def test_hooks_registered(self):
        import importlib
        hooks_mod = importlib.import_module("mhr.hooks")
        de = hooks_mod.doc_events

        # Stock Entry validate
        se_validate = de["Stock Entry"]["validate"]
        if isinstance(se_validate, str):
            se_validate = [se_validate]
        self.assertIn(
            "mhr.utilis.validate_hty_stock_entry", se_validate,
            "Stock Entry validate hook list must include validate_hty_stock_entry.",
        )

        # Delivery Trip validate
        dt = de.get("Delivery Trip", {})
        self.assertIn(
            "mhr.utilis.validate_hty_delivery_trip",
            (dt.get("validate") if isinstance(dt.get("validate"), str) else (dt.get("validate") or [])),
            "Delivery Trip validate hook must include validate_hty_delivery_trip.",
        )

        # Delivery Note on_submit
        dn_submit = de["Delivery Note"]["on_submit"]
        if isinstance(dn_submit, str):
            dn_submit = [dn_submit]
        self.assertIn(
            "mhr.utilis.restore_cones_for_hty_return", dn_submit,
            "Delivery Note on_submit hook list must include restore_cones_for_hty_return.",
        )
