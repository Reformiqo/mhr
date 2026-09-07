"""MI1-I129 (Rohit 2026-09-07) — Sales Order Container No: look the number up
only once it has been entered.

`custom_container_no` is a Data field. frappe's Data control runs the field
handler 500 ms after every pause in typing (`ControlData.bind_change_event`,
`trigger_change_on_input_event`), so the VFY booking script looked "MC" up
and announced "No lots found for container MC" while the user was still
typing. Both container handlers (VFY Client Script, HTY sales_order_hty.js)
now return while the input has focus and arm a one-shot blur listener that
re-runs the handler on the final value; Enter blurs the input.
"""

import json
import os
import re

import frappe
from frappe.tests.utils import FrappeTestCase

FIXTURE = os.path.join(frappe.get_app_path("mhr"), "fixtures", "client_script.json")
HTY_JS = os.path.join(frappe.get_app_path("mhr"), "public", "js", "sales_order_hty.js")


def _booking_script():
    with open(FIXTURE) as f:
        for cs in json.load(f):
            if cs["name"] == "Sales Order Booking":
                return cs


class TestFrappeStillFiresOnInput(FrappeTestCase):
    """The premise: without a guard the handler runs mid-typing."""

    def test_data_control_triggers_change_on_input(self):
        path = os.path.join(frappe.get_app_path("frappe"), "public", "js", "frappe", "form", "controls", "data.js")
        with open(path) as f:
            src = f.read()
        self.assertIn("static trigger_change_on_input_event = true;", src)
        self.assertRegex(src, r'\$input\.on\("input",\s*frappe\.utils\.debounce\(change_handler')


class TestVfyBookingScript(FrappeTestCase):

    def setUp(self):
        self.cs = _booking_script()
        self.src = self.cs["script"]

    def test_handler_waits_for_the_field_to_be_left(self):
        handler = self.src[self.src.index("custom_container_no(frm) {"):self.src.index("custom_fetch_by(frm) {")]
        self.assertIn("if (mi1_so_still_typing(frm, 'custom_container_no')) return;", handler)
        self.assertLess(handler.index("mi1_so_still_typing"), handler.index("mi1_so_open_lot_picker(frm);"),
                        "the guard runs before the lookup")
        # Clearing the field still resets the dependants at once.
        self.assertLess(handler.index("frm.set_value('custom_lot_no', '');"), handler.index("mi1_so_still_typing"))

    def test_guard_semantics(self):
        fn = self.src[self.src.index("function mi1_so_still_typing(frm, fieldname) {"):self.src.index("function mi1_so_open_lot_picker(frm) {")]
        self.assertIn("$input.is(':focus')", fn, "focused input = still typing")
        self.assertIn("return false;", fn)
        self.assertIn("$input.one('blur'", fn, "one-shot re-run when the field is left")
        self.assertIn("frm.trigger(fieldname);", fn)
        self.assertIn("if (e.key === 'Enter') $input.blur();", fn, "Enter confirms the value")
        self.assertIn("removeData('mhr_lookup_on_leave')", fn, "the guard re-arms for the next edit")

    def test_no_lots_message_kept_for_a_complete_unknown_number(self):
        self.assertIn("No lots found for container {0}", self.src)
        self.assertIn("mi1_so_explain_no_available_lot(container_no);", self.src)

    def test_fixture_modified_bumped_and_enabled(self):
        self.assertEqual(self.cs["enabled"], 1)
        self.assertGreater(self.cs["modified"], "2026-09-04 19:22:22.094195")
        self.assertNotIn("\r\n", self.src)

    def test_local_client_script_matches_fixture(self):
        if not frappe.db.exists("Client Script", "Sales Order Booking"):
            self.skipTest("Client Script not installed on this bench.")
        self.assertIn("mi1_so_still_typing", frappe.db.get_value("Client Script", "Sales Order Booking", "script"))


class TestHtyScript(FrappeTestCase):

    def test_hty_handler_uses_the_same_rule(self):
        with open(HTY_JS) as f:
            src = f.read()
        handler = src[src.index("custom_container_no: function (frm) {"):src.index("custom_fetch_by: function (frm) {")]
        self.assertIn("if (so_hty_still_typing(frm, 'custom_container_no')) return;", handler)
        self.assertLess(handler.index("so_hty_still_typing"), handler.index("so_hty_open_lot_popup(frm);"))
        fn = src[src.index("function so_hty_still_typing(frm, fieldname) {"):src.index("function so_hty_open_lot_popup(frm) {")]
        for needle in ("$input.is(':focus')", "$input.one('blur'", "frm.trigger(fieldname);", "if (e.key === 'Enter') $input.blur();"):
            self.assertIn(needle, fn)
