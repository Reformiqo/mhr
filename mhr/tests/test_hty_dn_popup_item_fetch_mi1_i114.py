"""MI1-I114 (Raj 2026-08-29) — HTY Delivery Note: Container popup & item fetch.

Raj: entering Container No only filled Notes (no popup); entering Denier
opened the popup but Select left the Item table empty. Prod (MCDL-07-24618):
the container's two batches were already delivered in full, so both
fetchers correctly found no stock — and the form said nothing about it.

Fix: the two popup triggers follow the same rules (HTY batches that hold
stock), the Select handler carries the warehouse holding that stock into the
row, and every empty outcome is spoken instead of silent.
"""
import inspect
import json
import os

import frappe
from frappe.tests.utils import FrappeTestCase

FIXTURE = os.path.join(frappe.get_app_path("mhr"), "fixtures", "client_script.json")
HTY_ITEM = "N6 J2700"        # HTY-only item on this bench, 300+ batches with stock
HTY_CONTAINER = "MCZFT-01"   # submitted HTY container with partly delivered stock


def _fixture_script(name="HTY & VFY"):
    with open(FIXTURE, encoding="utf-8") as fh:
        for cs in json.load(fh):
            if cs.get("name") == name:
                return cs
    raise AssertionError(f"{name} missing from fixtures.")


class TestItemFetcherAvailableOnly(FrappeTestCase):
    """mhr.note.get_hty_batches_by_item(..., only_available=1, transaction_type='HTY')."""

    def setUp(self):
        if not frappe.db.exists("Batch", {"item": HTY_ITEM, "custom_transaction_type": "HTY"}):
            self.skipTest(f"No HTY batches of {HTY_ITEM} on this bench.")

    def _pages(self, **kw):
        from mhr.note import get_hty_batches_by_item
        rows, page = [], 0
        while True:
            chunk = get_hty_batches_by_item(HTY_ITEM, page * 50, 50, **kw)
            if not chunk:
                break
            rows += chunk
            page += 1
            if len(chunk) < 50:
                break
        return rows

    def test_every_row_holds_stock_and_is_hty(self):
        rows = self._pages(only_available=1, transaction_type="HTY")
        self.assertTrue(rows, "Bench has HTY stock for this item; the fetcher must return it.")
        for r in rows:
            self.assertGreater(float(r["batch_qty"]), 0, f"{r['name']} shown without stock.")
            self.assertEqual(r["custom_transaction_type"], "HTY")
            self.assertTrue(r.get("warehouse"), "Row must name the warehouse holding the balance.")
            self.assertAlmostEqual(float(r["available_qty"]), float(r["batch_qty"]), places=6)

    def test_pages_are_cut_after_the_stock_filter(self):
        """The client keeps paging until a short page; pages must not overlap
        and must cover every stock-holding batch exactly once."""
        from mhr.note import get_hty_batches_by_item
        p0 = get_hty_batches_by_item(HTY_ITEM, 0, 50, only_available=1, transaction_type="HTY")
        p1 = get_hty_batches_by_item(HTY_ITEM, 50, 50, only_available=1, transaction_type="HTY")
        self.assertFalse({r["name"] for r in p0} & {r["name"] for r in p1}, "Pages overlap.")
        names = [r["name"] for r in self._pages(only_available=1, transaction_type="HTY")]
        self.assertEqual(len(names), len(set(names)), "A batch appeared on two pages.")
        expected = frappe.db.sql("""
            SELECT COUNT(*) FROM (
              SELECT sbe.batch_no, SUM(sbe.qty) bal
              FROM `tabSerial and Batch Bundle` sbb
              JOIN `tabSerial and Batch Entry` sbe ON sbe.parent = sbb.name
              JOIN `tabBatch` b ON b.name = sbe.batch_no
              WHERE b.item = %s AND b.custom_transaction_type = 'HTY' AND b.disabled = 0
                AND sbb.docstatus = 1 AND sbb.is_cancelled = 0
                AND sbb.type_of_transaction IN ('Inward', 'Outward')
              GROUP BY sbe.batch_no HAVING bal > 0) x""", HTY_ITEM)[0][0]
        self.assertEqual(len(names), expected)

    def test_other_mode_yields_nothing_for_an_hty_only_item(self):
        self.assertEqual(self._pages(only_available=1, transaction_type="VFY"), [])

    def test_default_call_is_unchanged(self):
        """Other callers keep the historical contract: every batch of the item,
        zero-balance rows kept (batch_qty 0) for pagination math."""
        from mhr.note import get_hty_batches_by_item
        sig = inspect.signature(get_hty_batches_by_item)
        self.assertEqual(list(sig.parameters), ["item", "limit_start", "limit_page_length", "only_available", "transaction_type"])
        self.assertEqual(sig.parameters["only_available"].default, 0)
        self.assertIsNone(sig.parameters["transaction_type"].default)
        self.assertIn("_clamp_batch_qty_to_available(batches, False)", inspect.getsource(get_hty_batches_by_item))
        self.assertEqual(get_hty_batches_by_item(None), [])


class TestContainerFetcherModeScope(FrappeTestCase):
    """mhr.utilis.get_container_batches_with_stock(container_no, transaction_type=None)."""

    def setUp(self):
        if not frappe.db.exists("Container", {"container_no": HTY_CONTAINER, "transaction_type": "HTY", "docstatus": 1}):
            self.skipTest(f"{HTY_CONTAINER} not on this bench.")

    def test_hty_scope_matches_default_for_an_hty_container(self):
        from mhr.utilis import get_container_batches_with_stock
        default = get_container_batches_with_stock(HTY_CONTAINER)
        hty = get_container_batches_with_stock(HTY_CONTAINER, "HTY")
        self.assertTrue(hty, "Bench has stock for this container.")
        self.assertEqual([r["name"] for r in hty], [r["name"] for r in default])
        for r in hty:
            self.assertGreater(float(r["batch_qty"]), 0)
            self.assertTrue(r.get("warehouse"))

    def test_other_mode_yields_nothing(self):
        from mhr.utilis import get_container_batches_with_stock
        self.assertEqual(get_container_batches_with_stock(HTY_CONTAINER, "VFY"), [])
        self.assertEqual(get_container_batches_with_stock("", "HTY"), [])

    def test_signature_is_additive(self):
        from mhr.utilis import get_container_batches_with_stock
        sig = inspect.signature(get_container_batches_with_stock)
        self.assertEqual(list(sig.parameters), ["container_no", "transaction_type"])
        self.assertIsNone(sig.parameters["transaction_type"].default)


class TestClientScriptHtyVfy(FrappeTestCase):
    """The 'HTY & VFY' Delivery Note Client Script, as shipped in fixtures."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.cs = _fixture_script()
        cls.src = cls.cs["script"].replace("\r\n", "\n")

    def test_fixture_record_was_bumped_so_migrate_applies_it(self):
        self.assertGreater(str(self.cs["modified"]), "2026-09-04",
                           "import_file_by_path skips a record whose modified is not newer than the DB's.")
        self.assertEqual(self.cs["enabled"], 1)

    def test_db_matches_fixture(self):
        db = (frappe.db.get_value("Client Script", "HTY & VFY", "script") or "").replace("\r\n", "\n")
        self.assertEqual(db, self.src, "The bench's script and the fixture drifted apart.")

    def test_container_fetcher_scoped_to_hty(self):
        self.assertIn("method: 'mhr.utilis.get_container_batches_with_stock',", self.src)
        self.assertIn("args: { container_no: container_no, transaction_type: 'HTY' },", self.src)

    def test_denier_fetcher_asks_for_stock_holding_hty_batches(self):
        start = self.src.find("async function get_all_batches_by_item(item)")
        body = self.src[start:start + 1800]
        self.assertIn("mhr.note.get_hty_batches_by_item", body)
        self.assertIn("only_available: 1,", body)
        self.assertIn("transaction_type: 'HTY',", body)

    def test_container_path_explains_a_known_container_without_stock(self):
        self.assertEqual(self.src.count("async function hty_explain_no_available_stock(container_no)"), 1)
        self.assertIn("frappe.db.count('Batch', {", self.src)
        self.assertIn("await hty_explain_no_available_stock(frm.doc.custom_container_no);", self.src)
        self.assertIn("use Resubmit if it is missing", self.src,
                      "The message must point at the Container's inward / Resubmit — the real cause of a stockless container.")
        # MI1-I71's silence for a partly typed number survives: no batch known → no message.
        self.assertIn("if (!known) return;", self.src)

    def test_denier_path_speaks_when_nothing_is_available(self):
        self.assertIn("No HTY batch of {0} has available stock.", self.src)

    def test_select_handler_sets_warehouse_and_never_closes_empty(self):
        self.assertIn("warehouse:                data.warehouse || frm.doc.set_warehouse || null,", self.src)
        self.assertIn("if (added_count === 0) {", self.src)
        self.assertIn("were skipped: already in the items table, or without available stock.", self.src)
        self.assertIn("Number(data.batch_qty) > 0", self.src, "MI1-I71 zero-qty guard stays.")

    def test_vfy_branch_untouched(self):
        """The VFY container popup still uses its own two-column fetcher."""
        self.assertIn("async function get_all_batches_vfy(container_no)", self.src)
        self.assertIn('if (frm.doc.transaction_type === "VFY") {', self.src)
        self.assertIn("let batches = await get_all_batches_vfy(frm.doc.custom_container_no);", self.src)


class TestNotesColumnAndSupersededProdCopy(FrappeTestCase):
    """Prod carried a second, enabled, hand-made copy of the script
    ("HTY & VFY final popup", test@reformiqo.com, 2026-08-27 → 08-31) whose
    one addition was a Notes column. Two enabled copies register the same
    triggers and redefine the same globals — the copy is shipped disabled
    and its column lives in the shipped script."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with open(FIXTURE, encoding="utf-8") as fh:
            cls.data = json.load(fh)
        cls.dn = [r for r in cls.data if r.get("dt") == "Delivery Note"]
        cls.src = _fixture_script()["script"].replace("\r\n", "\n")

    def test_notes_column_merged_into_the_shipped_popup(self):
        for pin in ("async function get_container_notes_map(batches)",
                    "function get_batch_notes(batch, notes_map)",
                    "async function show_hty_batch_dialog(frm, batches)",
                    "const notes_map = await get_container_notes_map(batches);",
                    "<th>Type</th><th>Colour</th><th>Grade</th><th>Notes</th>",
                    "const col_count = 15;"):
            self.assertIn(pin, self.src, pin)

    def test_prod_copy_ships_disabled_and_newer_than_prods_edit(self):
        copy = next((r for r in self.dn if r["name"] == "HTY & VFY final popup"), None)
        self.assertIsNotNone(copy, "The prod-only copy must be in fixtures so migrate can disable it.")
        self.assertEqual(copy["enabled"], 0)
        self.assertEqual(copy["module"], "Mhr")
        self.assertGreater(str(copy["modified"]), "2026-08-31 15:01:53.259358",
                           "import_file_by_path only applies a record newer than prod's.")
        self.assertIn("DISABLED — superseded (MI1-I114", copy["script"])

    def test_exactly_one_enabled_script_owns_the_popup_triggers(self):
        for trigger in ("async custom_container_no(frm)", "async custom_denier(frm)"):
            owners = [r["name"] for r in self.dn if r.get("enabled") and trigger in (r.get("script") or "")]
            self.assertEqual(owners, ["HTY & VFY"], f"{trigger}: {owners}")
