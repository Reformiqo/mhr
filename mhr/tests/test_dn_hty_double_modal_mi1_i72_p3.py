"""MI1-I72 P3 (Raj 2026-07-10) — after picking a batch in the HTY
'Select Batch' modal, a SECOND modal was opening on top of the first.

Root cause: show_hty_batch_dialog's primary_action did
    frm.set_value('custom_denier', last_batch.item || '');
which triggered the same script's `async custom_denier(frm)` handler.
That handler, for HTY, called show_hty_batch_dialog again.

Fix: write custom_denier directly on frm.doc (bypasses the trigger
cascade) and call refresh_field.
"""
import json
import os

import frappe
from frappe.tests.utils import FrappeTestCase


def _hty_vfy_script():
    path = os.path.join(frappe.get_app_path("mhr"), "fixtures", "client_script.json")
    with open(path) as fh:
        data = json.load(fh)
    for cs in data:
        if cs.get("name") == "HTY & VFY":
            return cs.get("script", "")
    raise AssertionError("'HTY & VFY' Client Script missing from fixtures.")


class TestNoSecondModalFromPickerDenierSetValue(FrappeTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.src = _hty_vfy_script()

    def test_marker_present(self):
        self.assertIn("MI1-I72 P3", self.src,
            "The batch picker's primary_action must carry the MI1-I72 P3 "
            "marker where the set_value → doc.custom_denier swap lives.")

    def test_picker_does_not_set_value_custom_denier(self):
        """The specific line that was firing the second modal."""
        self.assertNotIn(
            "frm.set_value('custom_denier',  last_batch.item",
            self.src,
            "Batch-picker primary_action must NOT call set_value on "
            "custom_denier — it re-triggers custom_denier handler which "
            "opens another modal. Use direct doc assignment.",
        )

    def test_picker_uses_direct_doc_assignment(self):
        self.assertIn(
            "frm.doc.custom_denier = last_batch.item",
            self.src,
            "Batch-picker primary_action must write custom_denier via "
            "frm.doc directly so the async custom_denier handler doesn't "
            "re-fire and open a second modal.",
        )
        self.assertIn(
            "frm.refresh_field('custom_denier');",
            self.src,
            "After direct doc assignment, refresh_field must run so the "
            "user sees the new value.",
        )


class TestOtherPickerHeaderCopiesUseSetValue(FrappeTestCase):
    """Only custom_denier needs the direct-assignment trick — the rest
    of the header copies (custom_glue, custom_pulp, custom_lusture,
    custom_grade, custom_lot_no, custom_fsc, custom_cone) have no
    modal-opening handlers, so leaving them as set_value is correct
    (they keep firing legitimate downstream reactions like fetch_from
    cascades from custom_batch)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.src = _hty_vfy_script()

    def test_other_fields_still_use_set_value(self):
        for f in ("custom_glue", "custom_pulp", "custom_lusture",
                  "custom_grade", "custom_lot_no", "custom_fsc",
                  "custom_cone"):
            self.assertIn(
                f"frm.set_value('{f}',",
                self.src,
                f"{f} must still use set_value — only custom_denier had "
                "the modal-cascade problem.",
            )


class TestCustomDenierHandlerIsHtyOnly(FrappeTestCase):
    """Pin: the async custom_denier(frm) handler must remain HTY-only.
    If someone widens it to VFY, this test flags them — and if they
    remove the whole handler, MI1-I72 P3 becomes moot (also flag)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.src = _hty_vfy_script()

    def test_denier_handler_present(self):
        self.assertIn("async custom_denier(frm)", self.src,
            "The custom_denier handler is the reason MI1-I72 P3 exists — "
            "if you remove it, this whole file becomes moot.")

    def test_denier_handler_gates_on_hty(self):
        self.assertIn(
            'if (frm.doc.transaction_type !== "HTY")',
            self.src,
            "custom_denier handler must early-return for non-HTY.",
        )
