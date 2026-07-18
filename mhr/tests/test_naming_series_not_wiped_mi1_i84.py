"""MI1-I84 (Raj 2026-07-18): custom Document Naming Series added
through Document Naming Settings must NOT be wiped on every deploy.

Root cause: mhr shipped 4 Property Setters (Delivery Trip, Delivery
Note, Stock Entry, Sales Order) in property_setter.json, each
setting `naming_series.options` to a hard-coded string. `bench
migrate` re-applies every Property Setter fixture on every deploy,
so anything the user had added via Document Naming Settings got
overwritten.

Fix has two halves:
  1. The 4 offending rows were removed from
     `mhr/fixtures/property_setter.json` — this stops the overwrite
     on the next migrate.
  2. `hooks.py` was updated with a filter that keeps
     `field_name = "naming_series"` OUT of every future
     `bench export-fixtures --app mhr` — so an admin's routine
     re-export doesn't silently put the overwrite back.

Together, the DB value of naming_series.options for these 4 doctypes
is preserved across all future deploys, with whatever custom series
users have added intact.
"""
import json
import os

import frappe
from frappe.tests.utils import FrappeTestCase


FIXTURE_PATH = os.path.join(
    frappe.get_app_path("mhr"), "fixtures", "property_setter.json"
)


def _load_fixture():
    with open(FIXTURE_PATH) as f:
        return json.load(f)


class TestFixtureNoLongerShipsNamingSeriesOptions(FrappeTestCase):
    """Half 1 — the immediate blocker for the next deploy."""

    def test_fixture_has_zero_naming_series_options_overrides(self):
        data = _load_fixture()
        offenders = [
            p for p in data
            if p.get("field_name") == "naming_series"
            and p.get("property") == "options"
        ]
        self.assertEqual(
            offenders, [],
            "mhr/fixtures/property_setter.json must NOT contain any "
            "Property Setter targeting `naming_series.options` — "
            "shipping those causes bench migrate to wipe user-added "
            "custom Naming Series on every deploy (MI1-I84).",
        )

    def test_specific_four_doctypes_removed(self):
        """The 4 doctypes that historically had a Property Setter must
        no longer appear in the fixture with field_name=naming_series."""
        data = _load_fixture()
        for dt in ("Delivery Trip", "Delivery Note", "Stock Entry", "Sales Order"):
            hit = [
                p for p in data
                if p.get("doc_type") == dt
                and p.get("field_name") == "naming_series"
            ]
            self.assertEqual(
                hit, [],
                f"Property Setter for {dt}.naming_series must be gone "
                f"from the fixture. Its re-application was the exact "
                f"MI1-I84 wipe.",
            )


class TestExportFilterKeepsThemOut(FrappeTestCase):
    """Half 2 — a future `bench export-fixtures --app mhr` must not
    silently reintroduce the entries by re-exporting Mhr Property
    Setters wholesale."""

    def test_property_setter_filter_excludes_naming_series(self):
        import mhr.hooks as hooks
        ps_entries = [
            f for f in hooks.fixtures
            if f.get("doctype") == "Property Setter"
        ]
        self.assertEqual(
            len(ps_entries), 1,
            "Expected exactly one Property Setter fixture rule.",
        )
        filters = ps_entries[0].get("filters", [])
        # The filter must include a negative on field_name == "naming_series".
        neg = [
            f for f in filters
            if isinstance(f, list) and len(f) == 3
            and f[0] == "field_name" and f[1] == "!=" and f[2] == "naming_series"
        ]
        self.assertEqual(
            len(neg), 1,
            "hooks.py fixtures rule for Property Setter must include "
            "`[\"field_name\", \"!=\", \"naming_series\"]` — otherwise "
            "a routine `bench export-fixtures --app mhr` puts the "
            "naming_series overrides back into the JSON and the next "
            "migrate wipes user additions again (MI1-I84).",
        )

    def test_module_filter_still_present(self):
        """Guard against accidentally dropping the module filter and
        exporting every Property Setter on the site."""
        import mhr.hooks as hooks
        ps_entries = [
            f for f in hooks.fixtures
            if f.get("doctype") == "Property Setter"
        ]
        filters = ps_entries[0].get("filters", [])
        mod = [
            f for f in filters
            if isinstance(f, list) and f[0] == "module" and f[1] == "in"
        ]
        self.assertEqual(
            len(mod), 1,
            "The module=in=Mhr filter must remain — dropping it would "
            "export every Property Setter on the site into mhr's "
            "fixtures.",
        )


class TestOtherPropertySettersStillShip(FrappeTestCase):
    """Regression: don't accidentally drop the legitimate Property
    Setters we DO want to ship."""

    def test_field_order_still_shipped(self):
        data = _load_fixture()
        hit = [
            p for p in data
            if p.get("doc_type") == "Delivery Note"
            and p.get("property") == "field_order"
        ]
        self.assertTrue(
            hit,
            "Delivery Note field_order Property Setter must still ship "
            "(it defines the form layout).",
        )

    def test_conversion_factor_in_list_view_still_shipped(self):
        data = _load_fixture()
        hit = [
            p for p in data
            if p.get("doc_type") == "Stock Entry Detail"
            and p.get("field_name") == "conversion_factor"
            and p.get("property") == "in_list_view"
        ]
        self.assertTrue(
            hit,
            "Stock Entry Detail.conversion_factor.in_list_view "
            "Property Setter must still ship.",
        )
