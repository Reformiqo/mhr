"""MI1-I71 double-popup (Raj 2026-07-16): two 'Select Batch' modals
opened when picking a batch on an HTY DN. Root cause — the
custom_denier handler fired concurrently with custom_container_no,
each opening its own show_hty_batch_dialog with a different filter
scope (item-only vs container-scoped).

Fix: gate the custom_denier handler on custom_container_no being
empty. When both are set (typical after auto-fetch from a batch),
only the container-scoped popup wins — the denier-scoped duplicate is
suppressed.

Also drops the 'No Batch found with Item' msgprint (silent-partial,
matching the earlier MI1-I71 fix).
"""
import json
import os

import frappe
from frappe.tests.utils import FrappeTestCase


def _script():
    path = os.path.join(frappe.get_app_path("mhr"), "fixtures", "client_script.json")
    with open(path) as fh:
        data = json.load(fh)
    for cs in data:
        if cs.get("name") == "HTY & VFY":
            return cs.get("script", "")
    raise AssertionError("HTY & VFY missing from fixtures.")


def _denier_handler_body(src):
    """Extract the async custom_denier(frm) function body — from the
    opening `{` to the matching `}`."""
    start = src.find("async custom_denier(frm)")
    assert start > -1, "custom_denier handler not found."
    # Find opening brace on this line.
    depth = 0
    i = start
    end = start
    started = False
    while i < len(src):
        ch = src[i]
        if ch == "{":
            depth += 1
            started = True
        elif ch == "}":
            depth -= 1
            if started and depth == 0:
                end = i + 1
                break
        i += 1
    return src[start:end]


class TestDenierHandlerGuardsOnContainerNo(FrappeTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.src = _script()
        cls.body = _denier_handler_body(cls.src)

    def test_marker_present(self):
        self.assertIn("MI1-I71 double-popup", self.body,
            "custom_denier handler must carry the MI1-I71 double-popup marker.")

    def test_short_circuits_when_container_no_set(self):
        self.assertIn(
            "if (frm.doc.custom_container_no) {",
            self.body,
            "custom_denier must early-return when custom_container_no is set — "
            "the container_no handler's popup is the authoritative one.",
        )
        # And the guard must return; not just log.
        idx = self.body.find("if (frm.doc.custom_container_no) {")
        block_end = self.body.find("}", idx)
        self.assertIn(
            "return;",
            self.body[idx:block_end],
            "The container_no guard must actually return; — not fall through.",
        )

    def test_no_msgprint_on_empty_batches(self):
        """MI1-I71 silent-partial — no nag when no batches match."""
        self.assertNotIn(
            "No Batch found with Item",
            self.body,
            "'No Batch found with Item' msgprint must be removed — silent "
            "early-return like the container_no branches.",
        )

    def test_still_opens_popup_when_denier_alone(self):
        """Regression pin: the handler must still open the popup when
        container_no is empty but denier is present — that's its
        original purpose (denier-only search)."""
        self.assertIn(
            "show_hty_batch_dialog(frm, batches);",
            self.body,
            "custom_denier must still open show_hty_batch_dialog when "
            "custom_container_no is empty — otherwise denier-only search "
            "stops working.",
        )

    def test_still_gates_on_hty(self):
        """Regression pin: VFY denier changes must not fire the HTY popup."""
        self.assertIn(
            'if (frm.doc.transaction_type !== "HTY") {',
            self.body,
            "custom_denier must remain HTY-only.",
        )


class TestContainerNoHandlerUnchanged(FrappeTestCase):
    """The container_no handler must not have been accidentally
    modified — this fix is one-directional (denier skips when
    container is set, not the other way)."""

    def test_container_no_still_calls_show_hty_batch_dialog(self):
        src = _script()
        # The custom_container_no async handler should still open the
        # popup in its HTY branch.
        self.assertIn(
            "async custom_container_no(frm)",
            src,
            "custom_container_no handler must still exist.",
        )
        # And its HTY branch should still call show_hty_batch_dialog.
        # Find the handler body and confirm.
        start = src.find("async custom_container_no(frm)")
        depth = 0
        i = start
        end = start
        started = False
        while i < len(src):
            ch = src[i]
            if ch == "{":
                depth += 1
                started = True
            elif ch == "}":
                depth -= 1
                if started and depth == 0:
                    end = i + 1
                    break
            i += 1
        body = src[start:end]
        self.assertIn(
            "show_hty_batch_dialog(frm, batches);",
            body,
            "custom_container_no HTY branch must still open the popup.",
        )
