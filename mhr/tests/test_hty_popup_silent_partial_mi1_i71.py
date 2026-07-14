"""MI1-I71 silent-partial (Raj 2026-07-14): partial-typed container_no
was firing 'No Batch found with Container No: mcz' etc. on every
keystroke that didn't fully match. Silent early-return is friendlier
— popup only shows when batches actually resolve.
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
    raise AssertionError("HTY & VFY script missing from fixtures.")


class TestMsgprintRemoved(FrappeTestCase):
    """Both live custom_container_no handler branches (VFY + HTY)
    must NOT fire a msgprint when batches.length === 0. Silent return
    only."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.src = _hty_vfy_script()

    def test_marker_present(self):
        self.assertIn("MI1-I71 silent-partial", self.src,
            "Client Script must carry the silent-partial marker.")

    def test_no_live_no_batch_found_msgprint(self):
        """Only comments (line // or block /*..*/) may still contain
        the phrase — no live msgprint may fire it."""
        for i, line in enumerate(self.src.split("\n"), 1):
            if "No Batch found with Container No" not in line:
                continue
            stripped = line.strip()
            if stripped.startswith("//"):
                # Line-commented — fine.
                continue
            # Is it inside a block comment?
            # A block-comment line lies after an unclosed /* and before */.
            prefix = "\n".join(self.src.split("\n")[:i - 1])
            opens = prefix.count("/*")
            closes = prefix.count("*/")
            if opens > closes:
                # Inside /* ... */ block — inert.
                continue
            self.fail(
                f"Live msgprint('No Batch found ...') survived at line {i}: "
                f"{stripped[:200]}",
            )

    def test_vfy_branch_still_clears_and_returns(self):
        """The silent-return must still call clear_batch_fields(frm)
        and return — otherwise the flow would carry on and try to open
        an empty popup."""
        # Look for the VFY branch's if (batches.length === 0) block
        # around get_all_batches_vfy.
        src = self.src
        idx = src.find("get_all_batches_vfy(frm.doc.custom_container_no)")
        self.assertGreater(idx, -1, "VFY fetcher call site must exist.")
        block = src[idx:idx + 800]
        self.assertIn("clear_batch_fields(frm);", block,
            "VFY empty-batches branch must clear header fields.")
        self.assertIn("return;", block,
            "VFY empty-batches branch must early-return so no popup opens.")

    def test_hty_branch_still_clears_and_returns(self):
        src = self.src
        idx = src.find("get_all_batches(frm.doc.custom_container_no)")
        self.assertGreater(idx, -1, "HTY fetcher call site must exist.")
        block = src[idx:idx + 800]
        self.assertIn("clear_batch_fields(frm);", block,
            "HTY empty-batches branch must clear header fields.")
        self.assertIn("return;", block,
            "HTY empty-batches branch must early-return so no popup opens.")
