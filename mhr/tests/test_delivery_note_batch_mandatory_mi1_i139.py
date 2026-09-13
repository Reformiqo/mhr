"""MI1-I139 (Raj 2026-09-12) — "the document is currently getting submitted
even when the Batch field is blank" for the Delivery Challan (Delivery
Note). Requirement: the header `custom_batch` (Link -> Batch) must be
mandatory before submission, with a clear validation message, enforced
server-side so it holds even for a manually created/edited document.

Scope: VFY only. Real data on this bench showed 5811/5818 submitted VFY
notes already carry `custom_batch` (the handful of blanks predate this
rule), while HTY notes rarely do (4 of 6 blank) — HTY tracks batches per
row (`items.batch_no`) instead of through this header field, so the same
rule there would newly block a working flow rather than close a real gap.
Confirmed with the user before implementing.

`custom_batch` itself was a live Custom Field on this bench with no
`module` set (created directly via Desk UI, like MI1-I138's "Send Mail"
Client Script), so `bench export-fixtures` never captured it — fixed
alongside this ticket (module set to Mhr, spliced into custom_field.json)
since a fresh install / other site would otherwise lack the field this fix
depends on.
"""
import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import today

from mhr.utilis import validate_delivery_challan_batch_mandatory

CUSTOMER = "Shree Ram Sevak Silk Mills"
ITEM = "58D/8F"
WH = "Finished Goods - MC"
COMPANY = "Meher Creations"


def _pick_batch():
    rows = frappe.db.sql("""
        SELECT b.name FROM `tabBatch` b
        JOIN (SELECT sbe.batch_no, SUM(sbe.qty) bal FROM `tabSerial and Batch Entry` sbe
              JOIN `tabSerial and Batch Bundle` sbb ON sbb.name = sbe.parent
              WHERE sbb.warehouse = %s AND sbb.docstatus = 1 AND sbb.is_cancelled = 0
                AND sbb.type_of_transaction IN ('Inward', 'Outward')
              GROUP BY sbe.batch_no HAVING bal >= 1) s ON s.batch_no = b.name
        WHERE b.item = %s AND b.custom_transaction_type = 'VFY' AND b.disabled = 0
        ORDER BY b.name LIMIT 1""", (WH, ITEM))
    return rows[0][0] if rows else None


def _masters_present():
    return frappe.db.exists("Customer", CUSTOMER) and frappe.db.exists("Item", ITEM) and _pick_batch()


class TestValidateDeliveryChallanBatchMandatoryUnit(FrappeTestCase):
    """Fast, isolated checks of the pure validation function — no DB writes."""

    def test_vfy_with_blank_batch_is_blocked(self):
        doc = frappe._dict(transaction_type="VFY", custom_batch=None)
        with self.assertRaises(frappe.ValidationError) as ctx:
            validate_delivery_challan_batch_mandatory(doc)
        self.assertIn("Batch is mandatory", str(ctx.exception))

    def test_vfy_with_empty_string_batch_is_blocked(self):
        doc = frappe._dict(transaction_type="VFY", custom_batch="")
        with self.assertRaises(frappe.ValidationError):
            validate_delivery_challan_batch_mandatory(doc)

    def test_vfy_with_batch_set_passes(self):
        doc = frappe._dict(transaction_type="VFY", custom_batch="SOME-BATCH")
        validate_delivery_challan_batch_mandatory(doc)  # must not raise

    def test_blank_transaction_type_defaults_to_vfy_and_is_blocked(self):
        """Blank = VFY, the same convention Delivery Challan / Delivery Trip
        Simplified already use for legacy documents."""
        doc = frappe._dict(transaction_type=None, custom_batch=None)
        with self.assertRaises(frappe.ValidationError):
            validate_delivery_challan_batch_mandatory(doc)

    def test_hty_with_blank_batch_is_exempt(self):
        doc = frappe._dict(transaction_type="HTY", custom_batch=None)
        validate_delivery_challan_batch_mandatory(doc)  # must not raise

    def test_hty_is_exempt_case_insensitively(self):
        doc = frappe._dict(transaction_type="hty", custom_batch=None)
        validate_delivery_challan_batch_mandatory(doc)  # must not raise

    def test_method_arg_ignored(self):
        doc = frappe._dict(transaction_type="VFY", custom_batch="SOME-BATCH")
        validate_delivery_challan_batch_mandatory(doc, method="before_submit")

    def test_error_message_matches_the_tickets_wording(self):
        doc = frappe._dict(transaction_type="VFY", custom_batch=None)
        with self.assertRaises(frappe.ValidationError) as ctx:
            validate_delivery_challan_batch_mandatory(doc)
        self.assertIn(
            "Batch is mandatory. Please select a Batch before submitting the Delivery Challan.",
            str(ctx.exception),
        )


class TestRealDocumentEndToEnd(FrappeTestCase):
    """A real Draft VFY Delivery Note cannot reach Submitted without a
    header Batch — proves the hook is actually wired through hooks.py,
    not just correct in isolation."""

    def setUp(self):
        if not _masters_present():
            self.skipTest("Bench lacks the VFY masters this suite is built on.")
        self.batch = _pick_batch()
        self.dn = None

    def tearDown(self):
        if self.dn and self.dn.name and frappe.db.exists("Delivery Note", self.dn.name):
            doc = frappe.get_doc("Delivery Note", self.dn.name)
            if doc.docstatus == 1:
                doc.cancel()
            frappe.delete_doc("Delivery Note", self.dn.name, force=1, ignore_permissions=True)

    def _make_dn(self, **header):
        dn = frappe.new_doc("Delivery Note")
        dn.update({
            "customer": CUSTOMER, "company": COMPANY, "transaction_type": "VFY",
            "posting_date": today(), "set_posting_time": 1, "set_warehouse": WH,
            "custom_sales_person": "Jayendrabhai",
            "selling_price_list": "Standard Selling", "currency": "INR",
        })
        dn.update(header)
        dn.append("items", {
            "item_code": ITEM, "qty": 1, "rate": 100, "warehouse": WH,
            "batch_no": self.batch, "use_serial_batch_fields": 1,
        })
        return dn

    def test_submit_blocked_without_batch(self):
        self.dn = self._make_dn(custom_batch=None)
        self.dn.insert(ignore_permissions=True)
        with self.assertRaises(frappe.ValidationError) as ctx:
            self.dn.submit()
        self.assertIn("Batch is mandatory", str(ctx.exception))
        self.dn.reload()
        self.assertEqual(self.dn.docstatus, 0, "a blocked submit must leave the document Draft")

    def test_submit_succeeds_with_batch(self):
        self.dn = self._make_dn(custom_batch=self.batch)
        self.dn.insert(ignore_permissions=True)
        self.dn.submit()
        self.assertEqual(self.dn.docstatus, 1)

    def test_draft_save_without_batch_is_not_blocked(self):
        """Only the final Submit is gated — a draft can be saved incrementally
        without a Batch, matching "before submission", not "on every save"."""
        self.dn = self._make_dn(custom_batch=None)
        self.dn.insert(ignore_permissions=True)  # must not raise
        self.assertEqual(self.dn.docstatus, 0)


class TestWiring(FrappeTestCase):

    def test_hook_registered_on_before_submit(self):
        import mhr.hooks as hooks
        self.assertIn(
            "mhr.utilis.validate_delivery_challan_batch_mandatory",
            hooks.doc_events["Delivery Note"]["before_submit"],
        )

    def test_custom_field_exists_live(self):
        self.assertTrue(frappe.db.exists("Custom Field", "Delivery Note-custom_batch"))

    def test_custom_field_module_is_mhr(self):
        """MI1-I139: the field predates this ticket but had no module set,
        so `bench export-fixtures` never captured it — fixed here."""
        self.assertEqual(frappe.db.get_value("Custom Field", "Delivery Note-custom_batch", "module"), "Mhr")

    def test_custom_field_is_in_the_fixture(self):
        import json
        import os
        path = os.path.join(frappe.get_app_path("mhr"), "fixtures", "custom_field.json")
        with open(path) as f:
            data = json.load(f)
        matches = [d for d in data if d.get("name") == "Delivery Note-custom_batch"]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["module"], "Mhr")
        self.assertEqual(matches[0]["fieldtype"], "Link")
        self.assertEqual(matches[0]["options"], "Batch")

    def test_custom_field_itself_stays_not_mandatory(self):
        """reqd stays 0 on the field definition — mandatory-ness is enforced
        by the before_submit hook (with a clear message), not a bare `reqd`
        Property Setter, so a draft can still be saved without it."""
        self.assertEqual(frappe.db.get_value("Custom Field", "Delivery Note-custom_batch", "reqd"), 0)
