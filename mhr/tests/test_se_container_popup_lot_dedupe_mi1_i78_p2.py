"""MI1-I78 P2 (Raj 2026-07-13): apply the DN VFY dedupe pattern to
the Stock Entry 'Select Batch' popup ('Stock Entry Container Info'
Client Script).

The SE popup had TWO problems:

  1. Single frappe.call with `limit_page_length: 100` — no paging.
     For MILA-04 (398 batches across 3 lots) the first 100 rows could
     all be one lot and the other lots never reached the client.

  2. Rendered one <tr> per Batch — even after paging, 100+ batches
     sharing one lot+cone bury the other lots behind the scroll.

Fix:
  * New `get_all_batches_se(container_no)` helper that pages through
    ALL batches (matches DN's get_all_batches_vfy pattern).
  * Dedupe by (custom_lot_no, custom_cone) before rendering rows so
    each unique combo shows exactly once.
  * primary_action still resolves via frappe.db.get_doc('Batch', name)
    on the first batch.name kept per combo — any batch with the same
    lot+cone populates identical header fields.
"""
import json
import os

import frappe
from frappe.tests.utils import FrappeTestCase


def _se_container_info_script():
    path = os.path.join(frappe.get_app_path("mhr"), "fixtures", "client_script.json")
    with open(path) as fh:
        data = json.load(fh)
    for cs in data:
        if cs.get("name") == "Stock Entry Container Info":
            return cs
    raise AssertionError(
        "'Stock Entry Container Info' Client Script missing from fixtures."
    )


class TestShippedViaFixtures(FrappeTestCase):

    def test_in_fixtures_and_mhr_module(self):
        cs = _se_container_info_script()
        self.assertEqual(
            cs.get("module"), "Mhr",
            "'Stock Entry Container Info' must live in the Mhr module so "
            "the fix ships via bench migrate.",
        )
        self.assertEqual(cs.get("enabled"), 1)
        self.assertEqual(cs.get("dt"), "Stock Entry")


class TestPagingHelperInstalled(FrappeTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.src = _se_container_info_script().get("script", "")

    def test_marker_present(self):
        self.assertIn("MI1-I78", self.src,
            "'Stock Entry Container Info' must carry the MI1-I78 marker.")

    def test_get_all_batches_se_defined(self):
        self.assertIn(
            "async function get_all_batches_se(container_no)",
            self.src,
            "SE popup must fetch batches via a paging helper. Single-call "
            "at limit_page_length:100 drops every batch past row 100.",
        )

    def test_pager_uses_a_while_loop(self):
        self.assertIn("while (true)", self.src,
            "The pager must loop until fewer than page_size batches "
            "come back — matches DN's get_all_batches_vfy pattern.")

    def test_pager_bumps_limit_start_per_page(self):
        self.assertIn("limit_start: page * page_size", self.src,
            "The pager must advance limit_start = page * page_size on "
            "each iteration.")

    def test_pager_terminates_on_short_page(self):
        self.assertIn("if (batches.length < page_size) break", self.src,
            "The pager must break when the current page returns fewer "
            "batches than page_size — otherwise it loops forever.")

    def test_old_single_call_limit_100_removed(self):
        """Regression pin: no lingering `limit_page_length: 100` single
        call — the old code capped the popup at 100 rows total."""
        self.assertNotIn(
            "limit_page_length: 100",
            self.src,
            "The old single-call `limit_page_length: 100` must be gone — "
            "it silently truncated large containers.",
        )


class TestDedupeInstalled(FrappeTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.src = _se_container_info_script().get("script", "")

    def test_uses_composite_lot_cone_key(self):
        self.assertIn(
            "(b.custom_lot_no || '') + '|' + (b.custom_cone != null ? b.custom_cone : '')",
            self.src,
            "Dedupe key must be (custom_lot_no, custom_cone). Note the "
            "`!= null` check on custom_cone so cone=0 doesn't collapse "
            "with cone=NULL.",
        )

    def test_uses_set_for_o1_lookup(self):
        self.assertIn("new Set()", self.src,
            "Dedupe must use a Set for O(1) lookup — the SE popup can "
            "receive 400+ batches per fetch.")

    def test_rows_render_from_unique_batches(self):
        self.assertIn(
            "unique_batches.map(batch =>",
            self.src,
            "The rendered rows_html must map over `unique_batches`, not "
            "the raw batches.",
        )


class TestPrimaryActionPreserved(FrappeTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.src = _se_container_info_script().get("script", "")

    def test_primary_action_reads_get_doc_batch(self):
        self.assertIn(
            "frappe.db.get_doc('Batch', selected)",
            self.src,
            "primary_action must still resolve the batch via "
            "frappe.db.get_doc('Batch', selected).",
        )

    def test_header_fields_populated_on_select(self):
        # SE popup writes header fields into different frappe fields
        # than DN does — cone lives on custom_se_cone, not custom_cone.
        for expected in (
            "frm.set_value('custom_glue',",
            "frm.set_value('custom_pulp',",
            "frm.set_value('custom_lusture',",
            "frm.set_value('custom_grade',",
            "frm.set_value('custom_lot_no',",
            "frm.set_value('custom_fsc',",
            "frm.set_value('custom_se_cone',",
            "frm.set_value('custom_denier',",
        ):
            self.assertIn(expected, self.src,
                f"SE popup primary_action must still call {expected!r}.")


class TestGetContainerNoStillWired(FrappeTestCase):
    """Regression pin — the SE handler used a `get_container_no` helper
    that resolves Container.container_no from the picked Container link.
    Pin it survived the rewrite."""

    def test_get_container_no_still_called(self):
        src = _se_container_info_script().get("script", "")
        self.assertIn("get_container_no(frm,", src,
            "SE handler must still call get_container_no to resolve the "
            "container_no from the Container Link before the batch fetch.")
