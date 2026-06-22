"""MI1-I62 — HTY Batch Label redesign tests.

Raj's PDF reference (`HTY BARCODE.pdf` on the ticket) sets the per-label
layout the system must print: Container No., Pallet No. (=Batch.custom_cone),
Den/Fil (=Batch.item), Cone (filament count parsed from item code), Net Wt
(=Batch.batch_qty), Gross Wt, Grade, Luster, Type (hardcoded "PALLET"),
Lot No. — plus a big serial top-right, QR code on the right, and a
caption underneath of the form "{cone}_{container}_{lot}".

Tests pin:
  - `mhr.utilis.hty_qr_data_url` exists, handles falsy input, and
    returns a valid `data:image/png;base64,` URL for real input.
  - hooks.py registers the helper in `jinja.methods` (else templates
    can't call it).
  - The HTY Batch Label print format references every required field
    plus the Jinja helper.
"""

import re

import frappe
from frappe.tests.utils import FrappeTestCase


class TestHtyQrDataUrlHelper(FrappeTestCase):

    def test_helper_exists(self):
        from mhr import utilis
        self.assertTrue(callable(getattr(utilis, "hty_qr_data_url", None)),
                        "mhr.utilis.hty_qr_data_url must exist.")

    def test_falsy_returns_empty(self):
        from mhr.utilis import hty_qr_data_url
        for v in (None, "", 0, False):
            self.assertEqual(hty_qr_data_url(v), "",
                f"hty_qr_data_url must return '' for falsy input ({v!r}).")

    def test_real_input_yields_png_data_url(self):
        from mhr.utilis import hty_qr_data_url
        url = hty_qr_data_url("130_MCCA-91_233D9025-1B")
        self.assertTrue(url.startswith("data:image/png;base64,"),
            "Output must be a PNG data URL prefix.")
        # Base64 payload should be non-trivial (a 24x24 QR with scale=4 is
        # well over 100 bytes encoded).
        b64 = url.split(",", 1)[1]
        self.assertGreater(len(b64), 100,
            "Base64 payload looks too short to be a valid QR PNG.")


class TestParseFilamentCount(FrappeTestCase):
    """The Cone field on the label is the leading digits after '/' in the
    item code (so '210/72 7.2 GPD' -> '72' and '58D/24F' -> '24')."""

    def test_helper_exists(self):
        from mhr import utilis
        self.assertTrue(callable(getattr(utilis, "hty_parse_filament_count", None)))

    def test_examples(self):
        from mhr.utilis import hty_parse_filament_count as f
        self.assertEqual(f("210/72 7.2 GPD"), "72")
        self.assertEqual(f("58D/24F"), "24",
            "'24F' must parse to '24' (leading digits only).")
        self.assertEqual(f("120D/48 F LOW MX"), "48")
        self.assertEqual(f("NOSLASH"), "",
            "No '/' in code -> '' (don't guess).")
        self.assertEqual(f(""), "")
        self.assertEqual(f(None), "")
        self.assertEqual(f("210/"), "",
            "Trailing slash with nothing after -> ''.")


class TestJinjaRegistration(FrappeTestCase):

    def test_helpers_registered_in_hooks(self):
        """Both helpers must be reachable from Jinja templates — that
        requires `jinja.methods` in hooks.py to include them."""
        import mhr.hooks as hooks_mod
        jinja_cfg = getattr(hooks_mod, "jinja", None)
        self.assertIsNotNone(jinja_cfg,
            "hooks.py must define a `jinja` dict for HTY Batch Label.")
        methods = (jinja_cfg or {}).get("methods", [])
        if isinstance(methods, str):
            methods = [methods]
        for required in (
            "mhr.utilis.hty_qr_data_url",
            "mhr.utilis.hty_parse_filament_count",
        ):
            self.assertIn(required, methods,
                f"`jinja.methods` in hooks.py must include {required!r}.")


class TestHtyBatchLabelFormat(FrappeTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        import json
        path = frappe.get_app_path(
            "mhr", "mhr", "print_format", "hty_batch_label", "hty_batch_label.json"
        )
        cls.fmt = json.load(open(path))
        cls.html = cls.fmt.get("html", "")

    def test_doctype_is_batch(self):
        self.assertEqual(self.fmt.get("doc_type"), "Batch")

    def test_jinja_helper_used(self):
        self.assertIn("hty_qr_data_url", self.html,
            "Template must call hty_qr_data_url to render the QR.")

    def test_required_field_labels_present(self):
        """Every label key on Raj's PDF must appear in the rendered template."""
        for label in (
            "Container No.", "Pallet No.", "Den/Fil", "Cone",
            "Net Wt", "Gross Wt", "Grade", "Luster", "Type", "Lot No.",
        ):
            self.assertIn(label, self.html,
                f"Required PDF label {label!r} missing from HTY Batch Label template.")

    def test_type_hardcoded_pallet(self):
        """Per the spec — 'Type' is hardcoded 'PALLET' for HTY labels."""
        self.assertRegex(self.html, r"Type</td>\s*<td class=\"v\">PALLET")

    def test_field_sources_match_spec(self):
        """Pin the mappings — Pallet No.=custom_cone, Den/Fil=item, etc."""
        expectations = {
            "Container No.": "custom_container_no",
            "Pallet No.": "custom_cone",
            "Net Wt": "batch_qty",
            "Grade": "custom_grade",
            "Luster": "custom_lusture",
            "Lot No.": "custom_lot_no",
        }
        for label, fieldname in expectations.items():
            # Look for the label row immediately followed by the field
            # reference within the same row (lenient — any whitespace allowed).
            pattern = re.compile(
                re.escape(label) + r".{0,180}?\bdoc\." + re.escape(fieldname) + r"\b",
                re.DOTALL,
            )
            self.assertRegex(self.html, pattern,
                f"Label {label!r} must be backed by doc.{fieldname}.")

    def test_serial_uses_supplier_batch_no_fallback_name(self):
        """Top-right serial = Batch.custom_supplier_batch_no, falling back
        to doc.name if no supplier batch is set."""
        self.assertRegex(
            self.html,
            r"doc\.custom_supplier_batch_no\s+or\s+doc\.name",
            "Top-right serial must default to custom_supplier_batch_no with "
            "doc.name as a fallback.",
        )

    def test_cone_value_uses_parse_helper(self):
        """The label's 'Cone' field uses hty_parse_filament_count(item)
        so '24F' parses to '24' (leading digits only), not just split-take.
        Pin that the template invokes the helper."""
        self.assertIn("hty_parse_filament_count", self.html,
            "Template must call hty_parse_filament_count(item_code) for Cone.")

    def test_page_size_set_via_css_field(self):
        """The doc's `css` field (NOT inline <style>) is where Frappe injects
        @page rules for wkhtmltopdf. NB does this; HTY must too — otherwise
        the page falls back to A4 and the label prints huge."""
        css = self.fmt.get("css", "") or ""
        self.assertIn("@page", css,
            "css field must declare @page (size + margins).")
        self.assertIn("105mm 95mm", css,
            "Page size must be ~A6 (105mm x 95mm) to match the reference PDF.")
