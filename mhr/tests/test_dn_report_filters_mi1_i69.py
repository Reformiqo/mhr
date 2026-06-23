"""MI1-I69 — DN Script Report (formerly Query Report).

Original bug: opening /query-report/DN exploded with KeyError on
from_date/transaction_type because Frappe's query_report client drops
empty-string filter values from the payload, leaving the SQL
placeholders unbound.

Followup (2026-06-23): converted DN from a Query Report to a Script
Report so column labels can swap dynamically with the Transaction Type
filter (Pulp ⇄ Type, Glue ⇄ Product, Lusture ⇄ Colour) — Frappe's
datatable wouldn't reliably re-render headers from a JS-mutated column
array. Script Reports build their column dict on every execute(),
which is the architecture Container Report + Balance Report already use.
"""

import json
import os
import re

import frappe
from frappe.tests.utils import FrappeTestCase


def _load_dn_report_fixture():
    path = os.path.join(frappe.get_app_path("mhr"), "fixtures", "report.json")
    with open(path) as f:
        data = json.load(f)
    for r in data:
        if r.get("name") == "DN":
            return r
    raise RuntimeError("DN report not found in fixtures/report.json")


class TestDnIsScriptReport(FrappeTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.report = _load_dn_report_fixture()

    def test_is_script_report(self):
        self.assertEqual(self.report.get("report_type"), "Script Report",
            "DN was converted from Query -> Script Report so column "
            "labels can swap with the Transaction Type filter.")

    def test_query_field_is_empty(self):
        # The SQL now lives in mhr/mhr/report/dn/dn.py — the fixture's
        # query/javascript fields should be empty.
        self.assertFalsy = lambda v: self.assertTrue(not v)
        self.assertFalsy(self.report.get("query"))
        self.assertFalsy(self.report.get("javascript"))

    def test_ref_doctype(self):
        self.assertEqual(self.report.get("ref_doctype"), "Delivery Note")


class TestDnScriptModule(FrappeTestCase):
    """The Python module backing the Script Report must exist + expose
    execute() + get_columns() with the right contract."""

    def test_module_loadable(self):
        mod = frappe.get_module(
            "mhr.mhr.report.dn.dn"
        )
        self.assertTrue(callable(getattr(mod, "execute", None)),
            "mhr.mhr.report.dn.dn.execute(filters) must exist.")
        self.assertTrue(callable(getattr(mod, "get_columns", None)),
            "get_columns(filters) must exist for label-swap testing.")

    def test_returns_columns_and_data_tuple(self):
        from mhr.mhr.report.dn.dn import execute
        cols, data = execute({"from_date": "1900-01-01", "to_date": "1900-01-01",
                              "transaction_type": "All"})
        self.assertIsInstance(cols, list)
        self.assertIsInstance(data, list)
        self.assertGreater(len(cols), 0)


class TestDnColumnLabelsSwapWithFilter(FrappeTestCase):
    """The whole point of the Script-Report conversion."""

    def _labels(self, transaction_type):
        from mhr.mhr.report.dn.dn import get_columns
        cols = get_columns({"transaction_type": transaction_type})
        return {c["fieldname"]: c["label"] for c in cols}

    def test_vfy_labels(self):
        labels = self._labels("VFY")
        self.assertEqual(labels["pulp"], "Pulp")
        self.assertEqual(labels["glue"], "Glue")
        self.assertEqual(labels["lusture"], "Lusture")

    def test_all_labels(self):
        """'All' uses the VFY-style labels (default view)."""
        labels = self._labels("All")
        self.assertEqual(labels["pulp"], "Pulp")
        self.assertEqual(labels["glue"], "Glue")
        self.assertEqual(labels["lusture"], "Lusture")

    def test_blank_labels(self):
        """Blank filter defaults to VFY-style labels."""
        labels = self._labels("")
        self.assertEqual(labels["pulp"], "Pulp")
        self.assertEqual(labels["glue"], "Glue")
        self.assertEqual(labels["lusture"], "Lusture")

    def test_hty_labels(self):
        labels = self._labels("HTY")
        self.assertEqual(labels["pulp"], "Type",
            "HTY mode must rename Pulp -> Type.")
        self.assertEqual(labels["glue"], "Product",
            "HTY mode must rename Glue -> Product.")
        self.assertEqual(labels["lusture"], "Colour",
            "HTY mode must rename Lusture -> Colour.")

    def test_fieldnames_stable_across_modes(self):
        """Labels swap but fieldnames must stay constant so data dicts
        always resolve regardless of mode."""
        from mhr.mhr.report.dn.dn import get_columns
        all_fns = [c["fieldname"] for c in get_columns({})]
        hty_fns = [c["fieldname"] for c in get_columns({"transaction_type": "HTY"})]
        self.assertEqual(all_fns, hty_fns,
            "fieldnames must be identical between VFY and HTY column lists.")


class TestDnRunsWithoutKeyError(FrappeTestCase):
    """Smoke-test: every transaction_type value must run without raising.
    The previous Query-Report incarnation raised KeyError when filters
    were missing or empty-string."""

    def _run(self, transaction_type):
        from frappe.desk.query_report import run as run_report
        return run_report(
            report_name="DN",
            filters={
                "from_date": "2026-06-01",
                "to_date": "2026-06-30",
                "transaction_type": transaction_type,
            },
            ignore_prepared_report=True,
        )

    def test_all(self):
        self._run("All")  # must not throw

    def test_vfy(self):
        self._run("VFY")

    def test_hty(self):
        self._run("HTY")

    def test_blank_transaction_type(self):
        """Defensive: passing '' as the transaction_type (Frappe drops
        empty filters from the payload, but tests can still pass it)
        must run without throwing."""
        self._run("")


class TestDnJsFilters(FrappeTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        path = os.path.join(
            frappe.get_app_path("mhr"),
            "mhr", "report", "dn", "dn.js",
        )
        cls.js = open(path).read()

    def test_from_to_transaction_type_filters_declared(self):
        for fname in ("from_date", "to_date", "transaction_type"):
            self.assertRegex(
                self.js,
                rf'"fieldname":\s*"{fname}"',
                f"DN JS must declare the {fname} filter.",
            )

    def test_transaction_type_default_all(self):
        self.assertTrue(
            re.search(
                r'"fieldname":\s*"transaction_type".*?"default":\s*"All"',
                self.js, re.DOTALL,
            ),
            "transaction_type must default to 'All' (Frappe drops "
            "empty-string filters from the request payload).",
        )

    def test_transaction_type_on_change_refresh(self):
        # on_change=refresh is what triggers the label swap when the
        # user toggles VFY/HTY/All — without it, the dropdown updates
        # the URL but the report stays stale.
        self.assertRegex(
            self.js,
            r"on_change:\s*function\s*\(\s*\)\s*\{\s*frappe\.query_report\.refresh\(\)",
            "transaction_type filter must call frappe.query_report.refresh() "
            "on change so the labels + data refresh immediately.",
        )
