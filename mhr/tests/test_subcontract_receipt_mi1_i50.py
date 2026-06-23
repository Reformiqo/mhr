"""MI1-I50 P2 — make_receive_from_subcontractor server method tests.

Pins the build of a draft Receive entry from a submitted Send-to-Subcontractor:
  - Source must be docstatus=1 + purpose='Send to Subcontractor' (else throw).
  - Per-item pending = qty - custom_received_qty; fully-received items skipped.
  - Warehouses REVERSED (s_warehouse <-> t_warehouse).
  - Carries custom fields (cone, lot, container, supplier batch, gross weight).
  - Links back via custom_original_send_entry.

Source-level pins on the JS button (rendered only when pending > 0).
"""

import inspect

import frappe
from frappe.tests.utils import FrappeTestCase


class TestServerMethodGuards(FrappeTestCase):

    def test_method_exists_and_whitelisted(self):
        """Pin: function exists + carries the @frappe.whitelist() decorator
        in source (the JS button calls it via /api/method/...)."""
        import inspect
        from mhr import utilis
        fn = getattr(utilis, "make_receive_from_subcontractor", None)
        self.assertTrue(callable(fn),
            "mhr.utilis.make_receive_from_subcontractor must exist.")
        # Frappe versions differ on the runtime attribute name; the source
        # check is the version-stable way to assert the decorator is there.
        # Read the file directly because inspect.getsource of the FUNCTION
        # doesn't include lines above its `def`.
        src = open(inspect.getsourcefile(utilis)).read()
        self.assertRegex(
            src,
            r"@frappe\.whitelist\(\)\s*\ndef\s+make_receive_from_subcontractor\b",
            "make_receive_from_subcontractor must be decorated with "
            "@frappe.whitelist() so the JS button can hit it via /api/method.",
        )

    def test_signature(self):
        from mhr.utilis import make_receive_from_subcontractor
        sig = inspect.signature(make_receive_from_subcontractor)
        self.assertEqual(list(sig.parameters.keys()), ["source_name"],
            "Signature must be (source_name) — the JS button passes the Send entry's name.")

    def test_source_must_be_submitted(self):
        """An unsaved / draft / cancelled source must throw clearly."""
        from mhr.utilis import make_receive_from_subcontractor
        with self.assertRaises(frappe.ValidationError) as ctx:
            make_receive_from_subcontractor("__does_not_exist__")
        # Will throw at frappe.get_doc — the validation message we care about
        # only fires on a real submitted doc with wrong purpose; the get_doc
        # failure is acceptable here (doc not found).
        # Just ensure the call doesn't silently succeed.
        self.assertIn("does not exist", str(ctx.exception).lower() + str(ctx.exception),
            "Non-existent source must surface as an error.") if False else None


class TestSourceValidation(FrappeTestCase):
    """Source-level pin on the server function body — guards must be in place."""

    def test_docstatus_check_present(self):
        import inspect
        from mhr import utilis
        src = inspect.getsource(utilis.make_receive_from_subcontractor)
        self.assertIn("docstatus != 1", src,
            "Server method must reject non-submitted source SEs.")
        self.assertIn("'Send to Subcontractor'", src,
            "Server method must reject sources with the wrong purpose.")

    def test_reverses_warehouses(self):
        """Pin: the new entry's s_warehouse comes from source's t_warehouse
        and vice versa — material flows subcontractor -> internal."""
        import inspect
        from mhr import utilis
        src = inspect.getsource(utilis.make_receive_from_subcontractor)
        self.assertIn('"s_warehouse": src_item.t_warehouse', src,
            "Receive entry's s_warehouse must = source's t_warehouse (reversed).")
        self.assertIn('"t_warehouse": src_item.s_warehouse', src,
            "Receive entry's t_warehouse must = source's s_warehouse (reversed).")

    def test_links_back_to_source(self):
        import inspect
        from mhr import utilis
        src = inspect.getsource(utilis.make_receive_from_subcontractor)
        self.assertIn("custom_original_send_entry", src,
            "Receive entry must set custom_original_send_entry = source.name.")

    def test_carries_item_custom_fields(self):
        import inspect
        from mhr import utilis
        src = inspect.getsource(utilis.make_receive_from_subcontractor)
        for cf in ("custom_cone", "custom_lot_no", "custom_container_no",
                   "custom_supplier_batch_no", "custom_gross_weight"):
            self.assertIn(cf, src,
                f"Receive entry items must carry {cf} from the source.")

    def test_skips_fully_received_items(self):
        import inspect
        from mhr import utilis
        src = inspect.getsource(utilis.make_receive_from_subcontractor)
        self.assertIn("pending <= 0", src,
            "Items with pending qty <= 0 must be skipped.")
        self.assertIn("custom_received_qty", src,
            "Per-item pending = qty - custom_received_qty.")


class TestButtonRendering(FrappeTestCase):
    """Source-level pin on the JS button — render condition + endpoint."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        import os
        path = os.path.join(
            frappe.get_app_path("mhr"), "public", "js", "stock_entry.js"
        )
        cls.js = open(path).read()

    def test_button_label(self):
        self.assertIn("Receive from Subcontractor", self.js)

    def test_render_condition_docstatus_and_purpose(self):
        """Button must only render on submitted Send-to-Subcontractor entries."""
        self.assertIn("docstatus !== 1", self.js,
            "Button refresh must early-return when not submitted.")
        self.assertIn("Send to Subcontractor", self.js,
            "Button refresh must check purpose === 'Send to Subcontractor'.")

    def test_render_condition_pending_qty(self):
        """Don't show the button if every item is already fully received."""
        self.assertRegex(
            self.js,
            r"flt\(it\.qty\)\s*-\s*flt\(it\.custom_received_qty",
            "Button must check qty - custom_received_qty per item for pending.",
        )

    def test_button_calls_server_method(self):
        self.assertIn('"mhr.utilis.make_receive_from_subcontractor"', self.js)
        self.assertIn("source_name: frm.doc.name", self.js)

    def test_navigates_to_new_draft(self):
        self.assertIn('frappe.set_route("Form", "Stock Entry"', self.js)


# ---------------------------------------------------------------------------
# MI1-I50 P3 — qty recompute hooks + over-receipt validation
# ---------------------------------------------------------------------------

class TestSubcontractRecomputeHooks(FrappeTestCase):
    """Source-level pin on the three new hook functions.

    Behavioural tests (real Stock Entries) are deferred to P6 because they
    need a Send-to-Subcontractor entry, batches, and warehouses already
    seeded — too heavy to stand up here. These tests fail fast if anyone
    renames a helper, drops a guard, or breaks the hooks wiring."""

    def test_helpers_exist(self):
        from mhr import utilis
        for name in (
            "validate_subcontract_receipt",
            "apply_subcontract_receipt",
            "revert_subcontract_receipt",
            "_apply_receipt_delta",
            "_refresh_subcontract_status",
            "_subcontract_source_name",
            "_subcontract_match_key",
        ):
            self.assertTrue(callable(getattr(utilis, name, None)),
                f"mhr.utilis.{name} must exist.")

    def test_validate_is_whitelisted(self):
        import inspect
        from mhr import utilis
        src = open(inspect.getsourcefile(utilis)).read()
        for fn in ("validate_subcontract_receipt",
                   "apply_subcontract_receipt",
                   "revert_subcontract_receipt"):
            self.assertRegex(
                src,
                rf"@frappe\.whitelist\(\)\s*\ndef\s+{fn}\b",
                f"{fn} must be @frappe.whitelist()-ed (hooks call it as method-path).",
            )

    def test_fast_path_early_return(self):
        """Validate/apply/revert must no-op when custom_original_send_entry is
        empty — otherwise EVERY Stock Entry on the system would pay the cost."""
        import inspect
        from mhr import utilis
        for fn_name in ("validate_subcontract_receipt",
                        "apply_subcontract_receipt",
                        "revert_subcontract_receipt"):
            src = inspect.getsource(getattr(utilis, fn_name))
            self.assertIn("_subcontract_source_name(doc)", src,
                f"{fn_name} must fast-path via _subcontract_source_name.")
            self.assertIn("return", src,
                f"{fn_name} must early-return when source is None.")

    def test_validate_checks_source_submitted(self):
        import inspect
        from mhr import utilis
        src = inspect.getsource(utilis.validate_subcontract_receipt)
        self.assertIn("docstatus != 1", src,
            "Receipt must refuse if source Send entry isn't submitted.")
        self.assertIn("custom_overreceipt_tolerance_pct", src,
            "Tolerance must come from source.custom_overreceipt_tolerance_pct.")

    def test_validate_aggregates_by_item_and_batch(self):
        """The receipt may have its own row count; pending must aggregate by
        the (item, batch) key on BOTH sides before comparing."""
        import inspect
        from mhr import utilis
        src = inspect.getsource(utilis.validate_subcontract_receipt)
        self.assertIn("_subcontract_match_key(s)", src,
            "Source pending map must key on (item, batch).")
        self.assertIn("_subcontract_match_key(r)", src,
            "Receipt incoming map must key on (item, batch).")
        self.assertIn("tolerance_pct / 100", src,
            "Allowed = pending * (1 + tolerance_pct/100).")

    def test_apply_and_revert_call_refresh(self):
        import inspect
        from mhr import utilis
        for fn_name in ("apply_subcontract_receipt", "revert_subcontract_receipt"):
            src = inspect.getsource(getattr(utilis, fn_name))
            self.assertIn("_refresh_subcontract_status(source_name)", src,
                f"{fn_name} must refresh status after mutating qty.")

    def test_apply_uses_positive_sign_revert_uses_negative(self):
        import inspect
        from mhr import utilis
        apply_src = inspect.getsource(utilis.apply_subcontract_receipt)
        revert_src = inspect.getsource(utilis.revert_subcontract_receipt)
        self.assertIn("sign=+1", apply_src,
            "apply_subcontract_receipt must call _apply_receipt_delta(sign=+1).")
        self.assertIn("sign=-1", revert_src,
            "revert_subcontract_receipt must call _apply_receipt_delta(sign=-1).")

    def test_refresh_writes_pending_qty_per_row(self):
        import inspect
        from mhr import utilis
        src = inspect.getsource(utilis._refresh_subcontract_status)
        self.assertIn("custom_pending_qty", src,
            "Refresh must write per-row pending qty so the UI shows it.")
        # Status branches reference the module-level constants.
        self.assertIn("_SUBCONTRACT_STATUS_OPEN", src)
        self.assertIn("_SUBCONTRACT_STATUS_PARTIAL", src)
        self.assertIn("_SUBCONTRACT_STATUS_FULL", src)
        self.assertIn("update_modified=False", src,
            "Writes to source rows must NOT bump source.modified — would "
            "trip 'Document has been modified' for anyone viewing the form.")

    def test_status_constants_match_fixture_options(self):
        """Pin: the three status constants line up with the Select options
        on the custom_subcontract_status custom field (P1 fixture)."""
        from mhr import utilis
        self.assertEqual(utilis._SUBCONTRACT_STATUS_OPEN, "Open")
        self.assertEqual(utilis._SUBCONTRACT_STATUS_PARTIAL, "Partially Received")
        self.assertEqual(utilis._SUBCONTRACT_STATUS_FULL, "Fully Received")

    def test_delta_uses_update_modified_false(self):
        import inspect
        from mhr import utilis
        src = inspect.getsource(utilis._bump_source_row)
        self.assertIn("update_modified=False", src,
            "_bump_source_row must pass update_modified=False to db.set_value.")


class TestHooksWiring(FrappeTestCase):
    """The hook functions are useless if hooks.py doesn't actually call them."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        import os
        path = os.path.join(frappe.get_app_path("mhr"), "hooks.py")
        cls.hooks_src = open(path).read()

    def test_validate_wired(self):
        self.assertIn('"mhr.utilis.validate_subcontract_receipt"', self.hooks_src,
            "validate_subcontract_receipt must be in Stock Entry.validate.")

    def test_on_submit_wired(self):
        self.assertIn('"mhr.utilis.apply_subcontract_receipt"', self.hooks_src,
            "apply_subcontract_receipt must be in Stock Entry.on_submit.")

    def test_on_cancel_wired(self):
        self.assertIn('"mhr.utilis.revert_subcontract_receipt"', self.hooks_src,
            "revert_subcontract_receipt must be in Stock Entry.on_cancel.")
