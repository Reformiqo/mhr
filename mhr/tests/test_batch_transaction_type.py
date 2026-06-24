"""Batch.custom_transaction_type — added 2026-06-24 per Raj's spec.

Pins:
  - Custom Field exists with the right shape (Link, list view, filter).
  - Validate hook is wired and auto-fetches from the linked Container.
  - Backfill patch is registered in patches.txt.
"""

import inspect
import os

import frappe
from frappe.tests.utils import FrappeTestCase


CF_NAME = "Batch-custom_transaction_type"


class TestCustomFieldShape(FrappeTestCase):

    def test_exists(self):
        self.assertTrue(frappe.db.exists("Custom Field", CF_NAME))

    def test_is_link_to_transaction_type(self):
        fieldtype, options = frappe.db.get_value(
            "Custom Field", CF_NAME, ["fieldtype", "options"]
        )
        self.assertEqual(fieldtype, "Link",
            "Must be a Link so the value is constrained to existing "
            "Transaction Type docs (VFY / HTY / future additions).")
        self.assertEqual(options, "Transaction Type")

    def test_shown_in_list_view_and_filter(self):
        in_list, in_filter = frappe.db.get_value(
            "Custom Field", CF_NAME, ["in_list_view", "in_standard_filter"]
        )
        self.assertTrue(in_list,
            "Must appear as a column in the Batch list view "
            "(Raj's spec).")
        self.assertTrue(in_filter,
            "Must be a standard filter so HTY/VFY user-permission "
            "rules can key off it.")


class TestValidateHook(FrappeTestCase):

    def test_helper_exists(self):
        from mhr import utilis
        self.assertTrue(
            callable(getattr(utilis, "set_batch_transaction_type_from_container", None)),
            "mhr.utilis.set_batch_transaction_type_from_container must exist.",
        )

    def test_hook_wired_in_hooks_py(self):
        from mhr import hooks
        batch_events = hooks.doc_events.get("Batch", {})
        validate_handlers = batch_events.get("validate", [])
        if isinstance(validate_handlers, str):
            validate_handlers = [validate_handlers]
        self.assertIn(
            "mhr.utilis.set_batch_transaction_type_from_container",
            validate_handlers,
            "Validate hook must be registered on Batch in hooks.py.",
        )

    def test_helper_skips_when_value_already_set(self):
        """fetch_if_empty semantics — never clobber a manual value."""
        from mhr import utilis
        src = inspect.getsource(utilis.set_batch_transaction_type_from_container)
        self.assertIn(
            'doc.get("custom_transaction_type")', src,
            "Helper must check the existing value first.",
        )

    def test_helper_resolves_via_container_no_lookup(self):
        """custom_container_no is varchar, not a Link — helper must
        look up Container by container_no, not by name."""
        from mhr import utilis
        src = inspect.getsource(utilis.set_batch_transaction_type_from_container)
        self.assertIn(
            '"container_no": container_no', src,
            "Helper must look up Container by container_no, not by name.",
        )

    def test_helper_fires_on_a_fake_doc(self):
        """End-to-end: feed the helper a minimal doc with a known
        container_no and assert the value gets set."""
        from mhr.utilis import set_batch_transaction_type_from_container
        # Pick any container_no that exists with a transaction_type set
        sample = frappe.db.sql(
            "SELECT container_no, transaction_type FROM `tabContainer` "
            "WHERE transaction_type IS NOT NULL AND transaction_type != '' LIMIT 1",
            as_dict=True,
        )
        if not sample:
            self.skipTest("No Container rows with transaction_type on this site.")
        cn = sample[0]["container_no"]
        expected = sample[0]["transaction_type"]

        class Doc:
            def __init__(self, container_no):
                self.custom_container_no = container_no
                self.custom_transaction_type = None
            def get(self, k, default=None):
                return getattr(self, k, default)

        d = Doc(cn)
        set_batch_transaction_type_from_container(d)
        self.assertEqual(d.custom_transaction_type, expected,
            f"Helper must set custom_transaction_type to {expected!r} "
            f"for container_no={cn!r}.")


class TestBackfillPatchRegistered(FrappeTestCase):

    def test_patch_in_patches_txt(self):
        path = os.path.join(frappe.get_app_path("mhr"), "patches.txt")
        body = open(path).read()
        self.assertIn(
            "mhr.patches.v1_0.backfill_batch_transaction_type",
            body,
            "Backfill patch must be registered in patches.txt.",
        )

    def test_patch_chunks_updates(self):
        """295k+ tabBatch rows — patch must chunk to avoid lock-wait
        timeouts. Pin CHUNK_SIZE + chunked loop pattern."""
        from mhr.patches.v1_0 import backfill_batch_transaction_type as p
        src = inspect.getsource(p)
        self.assertIn("CHUNK_SIZE", src,
            "Patch must define a chunk size for the UPDATE.")
        self.assertIn("for i in range(0, len(container_nos)", src,
            "Patch must loop over chunks of container_no values.")
        self.assertIn("(custom_transaction_type IS NULL", src,
            "Patch must only update rows whose value isn't already set "
            "(idempotent).")


class TestBackfillResult(FrappeTestCase):

    def test_no_orphan_batches_for_known_containers(self):
        """After the patch runs, every Batch whose custom_container_no
        matches a Container with a non-empty transaction_type must
        have custom_transaction_type populated."""
        orphans = frappe.db.sql(
            """
            SELECT COUNT(*) FROM `tabBatch` b
            WHERE b.custom_container_no IS NOT NULL
              AND b.custom_container_no != ''
              AND (b.custom_transaction_type IS NULL
                   OR b.custom_transaction_type = '')
              AND EXISTS (
                  SELECT 1 FROM `tabContainer` c
                  WHERE c.container_no = b.custom_container_no
                    AND c.transaction_type IS NOT NULL
                    AND c.transaction_type != ''
              )
            """
        )[0][0]
        self.assertEqual(orphans, 0,
            f"{orphans} Batch rows still missing custom_transaction_type "
            "after the backfill — the patch didn't cover them.")
