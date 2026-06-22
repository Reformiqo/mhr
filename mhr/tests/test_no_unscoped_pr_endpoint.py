"""Root cause of the MCJC-1517 duplicate-PR incident (2026-04-15).

Investigation showed `mhr.utilis` had a no-argument `@frappe.whitelist()
def create_purchase_receipt()` that picked a Container via
`frappe.get_last_doc('Container')` and never set `custom_container_no` on
the resulting PR. Any session hitting
`/api/method/mhr.utilis.create_purchase_receipt` three times produced three
identical Purchase Receipts for whichever Container was the most recently
modified at the moment of each call — orphaned (no Container link) and
indistinguishable from each other. That's what generated the 3 extra
JILIN PRs against the 66 batches of MCJC-1517-56-1.

Three sibling helpers (`get_items`, `get_item_batches`,
`create_serial_and_batch_bundle`) had the same defect: all called
`frappe.get_last_doc('Container')` with no scoping.

Pin: the unsafe endpoints stay removed; the remaining `create_purchase_receipt`
must be the container-scoped variant.
"""

import inspect
import re

import frappe
from frappe.tests.utils import FrappeTestCase

from mhr import utilis


class TestNoUnscopedPREndpoint(FrappeTestCase):

    def test_create_purchase_receipt_requires_container_arg(self):
        """The remaining `create_purchase_receipt` must be container-scoped —
        never zero-arg."""
        fn = getattr(utilis, "create_purchase_receipt", None)
        if fn is None:
            return  # fully removed is also acceptable
        sig = inspect.signature(fn)
        params = list(sig.parameters.keys())
        self.assertGreaterEqual(
            len(params), 1,
            "create_purchase_receipt must require a Container argument; "
            "the zero-arg variant caused the MCJC-1517 duplicate-PR bug.",
        )
        self.assertEqual(
            params[0], "container",
            "First param must be 'container' (the safe, scoped variant).",
        )

    def test_safe_variant_does_not_use_get_last_doc(self):
        """Belt + braces: the surviving variant must scope by its argument,
        not by frappe.get_last_doc('Container')."""
        fn = getattr(utilis, "create_purchase_receipt", None)
        if fn is None:
            return
        src = inspect.getsource(fn)
        self.assertNotIn(
            "get_last_doc", src,
            "create_purchase_receipt(container) must scope by its argument; "
            "get_last_doc('Container') was the root cause of MCJC-1517.",
        )

    def test_no_unscoped_whitelisted_helpers_in_source(self):
        """The four unsafe whitelisted endpoints (no-arg / single-arg) must
        not be present in utilis.py. Searching the file directly so even a
        copy-pasted reintroduction is caught."""
        src = open(inspect.getsourcefile(utilis)).read()
        for fn_name in (
            "get_items",
            "get_item_batches",
            "create_serial_and_batch_bundle",
            "create_purchase_receipt",
        ):
            # Match the unsafe shape: @frappe.whitelist() then `def name()`
            # or `def name(item_code)` — the exact signatures that picked a
            # Container via get_last_doc.
            pat = re.compile(
                r"@frappe\.whitelist\(\)\s*\n\s*def\s+"
                + re.escape(fn_name)
                + r"\s*\(\s*(?:item_code\s*)?\)\s*:",
                re.MULTILINE,
            )
            self.assertIsNone(
                pat.search(src),
                f"Unsafe whitelisted '{fn_name}' (unscoped, used "
                "frappe.get_last_doc('Container')) must stay removed — it "
                "was the root cause of the MCJC-1517 duplicate-PR bug.",
            )
