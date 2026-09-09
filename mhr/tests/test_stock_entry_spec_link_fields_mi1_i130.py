"""MI1-I130 (Rohit 2026-09-08) — Job Work Received: Glue / Pulp / Lusture /
Grade / FSC should be dropdown/selectable fields, like Container Inward, and
should default from the selected Received Container Number / Received Lot
No.

Container Inward's own Glue / Pulp / Lusture / Grade / FSC are Link fields
to Item Specification (Container.js scopes each dropdown to its own
specification_type — MI1-I80). Stock Entry's five custom fields of the same
name were plain Data, so Job Work Received showed grey text boxes instead of
a dropdown even though the values already stored there (custom_glue etc.)
are the exact same Item Specification docnames Container writes. Converting
them to the same Link type gives the identical dropdown, for free, on data
that was already compatible — verified against every non-blank value on this
bench before the change (0 mismatches).

Merge No and Cross Section are NOT converted: Container's own fields of
those names are plain Data too (screenshot 1 of the ticket shows Merge No as
a plain box, unfocused, on the very form the ticket points at as the
reference) — there is nothing to link them to.
"""

import json
import os

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import nowdate, flt

from mhr import utilis

FIXTURE = os.path.join(frappe.get_app_path("mhr"), "fixtures", "client_script.json")
CUSTOM_FIELD_FIXTURE = os.path.join(frappe.get_app_path("mhr"), "fixtures", "custom_field.json")
SCRIPT_NAME = "Stock Entry Container Info"

SPEC_LINK_FIELDS = ("custom_glue", "custom_pulp", "custom_lusture", "custom_grade", "custom_fsc")


def _script():
    with open(FIXTURE) as f:
        for cs in json.load(f):
            if cs["name"] == SCRIPT_NAME:
                return cs


def _custom_fields():
    with open(CUSTOM_FIELD_FIXTURE) as f:
        return {f["fieldname"]: f for f in json.load(f) if f["dt"] == "Stock Entry" and f["fieldname"] in SPEC_LINK_FIELDS}


def _get_or_create_spec(specification_type, value):
    name = f"{specification_type}-{value}"
    if not frappe.db.exists("Item Specification", name):
        frappe.get_doc({
            "doctype": "Item Specification", "specification_type": specification_type, "value": value,
        }).insert(ignore_permissions=True)
    return name


class TestFixtureDefinesLinkFields(FrappeTestCase):
    """The field-type change itself, and that it shipped consistently."""

    def setUp(self):
        self.fields = _custom_fields()

    def test_five_fields_are_now_links_to_item_specification(self):
        self.assertEqual(set(self.fields), set(SPEC_LINK_FIELDS))
        for fieldname, f in self.fields.items():
            self.assertEqual(f["fieldtype"], "Link", fieldname)
            self.assertEqual(f["options"], "Item Specification", fieldname)
            self.assertGreater(f["modified"], "2026-04-16 12:00:00", f"{fieldname} modified must be bumped")

    def test_merge_no_and_cross_section_are_untouched(self):
        with open(CUSTOM_FIELD_FIXTURE) as fh:
            d = json.load(fh)
        for f in d:
            if f["dt"] == "Stock Entry" and f["fieldname"] in ("custom_merge_no", "custom_cross_section", "custom_notes"):
                self.assertEqual(f["fieldtype"], "Data", f["fieldname"])
                self.assertIsNone(f["options"])

    def test_local_meta_matches_the_fixture(self):
        m = frappe.get_meta("Stock Entry")
        for fieldname in SPEC_LINK_FIELDS:
            field = m.get_field(fieldname)
            self.assertEqual(field.fieldtype, "Link", fieldname)
            self.assertEqual(field.options, "Item Specification", fieldname)


class TestClientScriptDropdownAndFetch(FrappeTestCase):

    def setUp(self):
        self.cs = _script()
        self.src = self.cs["script"]
        self.assertNotIn("\r\n", self.src, "this script has no CRLF; keep it that way")

    def test_setup_scopes_each_dropdown_to_its_specification_type(self):
        setup = self.src[self.src.index("setup(frm) {"):self.src.index("refresh(frm) {")]
        for field, spec_type in (("custom_glue", "Glue"), ("custom_pulp", "Pulp"), ("custom_lusture", "Lusture"),
                                 ("custom_grade", "Grade"), ("custom_fsc", "FSC")):
            self.assertIn(field, setup, field)
        self.assertIn("spec_type_by_field", setup)
        self.assertIn("frm.set_query(field, function() {", setup)
        self.assertIn("filters: { specification_type: spec_type }", setup)

    def test_no_doc_event_handler_on_the_two_fields(self):
        """MI1-I130 follow-up (2026-09-09, round 2): the work is wired
        directly to the native input via mhr_bind_leave in refresh(), not
        through a doc_event handler -- see test_still_typing_guard_is_a_
        pure_focus_check's replacement below for why frm.trigger(fieldname)
        is not a reliable signal here."""
        self.assertNotIn("custom_received_container_no(frm) {", self.src)
        self.assertNotIn("custom_received_lot_no(frm) {", self.src)
        self.assertIn("mhr_on_received_container_or_lot_leave(frm) {", self.src)
        handler = self.src[self.src.index("function mhr_on_received_container_or_lot_leave(frm) {"):
                           self.src.index("function mhr_input_value(frm, fieldname) {")]
        self.assertIn("if (!frm.doc.custom_original_send_entry) return;", handler)
        self.assertIn("fetch_received_container_spec(frm, container_no, lot_no);", handler)

    def test_leave_is_bound_eagerly_in_refresh_for_both_fields(self):
        """Round 1 (2026-09-09): a fast, continuous type-then-Enter
        walkthrough found Enter did nothing at all -- document.activeElement
        never changed. Binding was lazy, armed only from inside a helper
        that itself only ever ran via one of frappe's own triggers (a
        500 ms-debounced mid-typing path, or a blur-triggered path) -- type
        fast enough that neither ever fires before Enter, and the listener
        that would call .blur() on Enter was never attached. Fixed by
        binding unconditionally in refresh(), the moment the input exists."""
        refresh = self.src[self.src.index("refresh(frm) {"):self.src.index("custom_container_number: async function(frm) {")]
        self.assertIn("mhr_bind_leave(frm, 'custom_received_container_no', mhr_on_received_container_or_lot_leave);", refresh)
        self.assertIn("mhr_bind_leave(frm, 'custom_received_lot_no', mhr_on_received_container_or_lot_leave);", refresh)

    def test_leave_binds_directly_to_native_blur_not_frm_trigger(self):
        """Round 2 (2026-09-09): normal-speed typing then Tab stopped
        refetching once the guard was simplified to rely on
        frm.trigger(fieldname) firing again on blur. It doesn't reliably:
        frappe's model-diff (validate_and_set_in_model :: is_value_same)
        skips re-invoking the field's handler on blur once the mid-typing
        500 ms debounce has already written that same value into the model
        -- the ordinary case for anyone who doesn't pause before typing the
        next field. A listener bound straight to the input's own native
        'blur' event fires exactly once whenever focus leaves it, regardless
        of frappe's internal debounce/model-diff timing -- so the callback
        is called directly, never through frm.trigger or a doc_event."""
        fn = self.src[self.src.index("function mhr_bind_leave(frm, fieldname, on_leave) {"):self.src.index("function mhr_on_received_container_or_lot_leave(frm) {")]
        self.assertIn("$input.data('mhr_leave_bound')) return;", fn, "idempotent across refreshes")
        self.assertIn("$input.data('mhr_leave_bound', true);", fn)
        self.assertIn("if (e.key === 'Enter') $input.blur();", fn)
        self.assertIn("$input.on('blur', () => on_leave(frm));", fn)
        self.assertNotIn("frm.trigger(", fn, "must not depend on frappe's own field-change re-dispatch")
        self.assertNotIn(":focus", fn, "must bind unconditionally, not only while focused")

    def test_leave_reads_the_raw_dom_value_not_frm_doc(self):
        """Round 3 (2026-09-09): a fast type-then-immediate-Enter walkthrough
        found the fetch DID fire (rounds 1+2 both hold), but with the
        PREVIOUS lot number -- 'blur' fires before the browser's native
        'change' event, which is what frappe's change_handler listens for to
        write the typed value into frm.doc, and that write is itself a
        Promise chain (frappe.run_serially), not synchronous. Reading the
        input's own current text sidesteps the question of whether frappe's
        model write has caught up at all."""
        handler = self.src[self.src.index("function mhr_on_received_container_or_lot_leave(frm) {"):self.src.index("function mhr_input_value(frm, fieldname) {")]
        code_lines = "\n".join(l for l in handler.split("\n") if not l.strip().startswith("//"))
        self.assertIn("mhr_input_value(frm, 'custom_received_container_no')", code_lines)
        self.assertIn("mhr_input_value(frm, 'custom_received_lot_no')", code_lines)
        self.assertNotIn("frm.doc.custom_received_container_no", code_lines, "must not read the (possibly stale) model in executable code")
        self.assertNotIn("frm.doc.custom_received_lot_no", code_lines, "must not read the (possibly stale) model in executable code")
        raw = self.src[self.src.index("function mhr_input_value(frm, fieldname) {"):self.src.index("function fetch_received_container_spec(")]
        self.assertIn("field.$input.val()", raw)

    def test_fetch_accepts_explicit_values_and_falls_back_to_frm_doc(self):
        fn = self.src[self.src.index("function fetch_received_container_spec(frm, container_no, lot_no) {"):self.src.index("function clear_batch_fields_se(frm) {")]
        self.assertIn("if (container_no === undefined) container_no = frm.doc.custom_received_container_no;", fn)
        self.assertIn("if (lot_no === undefined) lot_no = frm.doc.custom_received_lot_no;", fn)

    def test_fetch_calls_the_endpoint_and_maps_every_field(self):
        fn = self.src[self.src.index("function fetch_received_container_spec(frm, container_no, lot_no) {"):self.src.index("function clear_batch_fields_se(frm) {")]
        self.assertIn("method: 'mhr.utilis.get_received_container_spec'", fn)
        self.assertIn("container_no: container_no", fn)
        self.assertIn("lot_no: lot_no", fn)
        self.assertIn("if (!spec) return;", fn, "no match -> leave the fields as they are")
        for src, field in (("glue", "custom_glue"), ("pulp", "custom_pulp"), ("lusture", "custom_lusture"),
                           ("grade", "custom_grade"), ("fsc", "custom_fsc"), ("merge_no", "custom_merge_no"),
                           ("cross_section", "custom_cross_section"), ("notes", "custom_notes")):
            self.assertIn(f"{src}:", fn.split("map = {")[1].split("};")[0], f"{src} -> {field} missing from the map")

    def test_fixture_modified_bumped_and_local_script_matches(self):
        self.assertGreater(self.cs["modified"], "2026-08-31 16:00:00.000000")
        db_script = frappe.db.get_value("Client Script", SCRIPT_NAME, "script") or ""
        self.assertEqual(db_script, self.src)


class TestSpecLinkValue(FrappeTestCase):
    """spec_link_value: the one place a value gets normalized, because
    Frappe validates every Link field inside insert()/save() BEFORE any
    doc_events 'validate' hook runs -- a validate-time normalizer would
    already be too late."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.grade = _get_or_create_spec("Grade", "MI1I130 TEST GRADE")

    def test_already_prefixed_value_is_kept(self):
        self.assertEqual(utilis.spec_link_value("Grade", self.grade), self.grade)

    def test_bare_hty_style_value_is_prefixed(self):
        """MI1-I107: an HTY Batch's custom_grade holds the bare value."""
        self.assertEqual(utilis.spec_link_value("Grade", "MI1I130 TEST GRADE"), self.grade)

    def test_unresolvable_value_is_returned_unchanged(self):
        self.assertEqual(utilis.spec_link_value("Grade", "No Such Grade At All"), "No Such Grade At All")

    def test_blank_is_blank(self):
        self.assertEqual(utilis.spec_link_value("Grade", ""), "")
        self.assertEqual(utilis.spec_link_value("Grade", None), "")

    def test_whitespace_is_trimmed(self):
        self.assertEqual(utilis.spec_link_value("Grade", f"  {self.grade}  "), self.grade)


class TestResolveContainerSpec(FrappeTestCase):
    """resolve_container_spec / get_received_container_spec: what the
    on-change handler asks for."""

    CONTAINER_NO = "MI1I130-TEST-CTR"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.glue = _get_or_create_spec("Glue", "MI1I130 GLUE")
        cls.pulp = _get_or_create_spec("Pulp", "MI1I130 PULP")
        cls.lusture_a = _get_or_create_spec("Lusture", "MI1I130 LUSTURE A")
        cls.lusture_b = _get_or_create_spec("Lusture", "MI1I130 LUSTURE B")
        cls.grade = _get_or_create_spec("Grade", "MI1I130 GRADE")
        cls.fsc = _get_or_create_spec("FSC", "MI1I130 FSC")
        cls.container_a = cls._make_container("L1", cls.lusture_a, "MERGE-A")
        cls.container_b = cls._make_container("L2", cls.lusture_b, "MERGE-B")

    @classmethod
    def _make_container(cls, lot_no, lusture, merge_no):
        """Insert directly and flip docstatus, skipping Container's heavy
        submit chain (creates a Purchase Receipt from its batches — MI1-I83's
        test uses the same shortcut for the same reason: only the plain
        field values matter here, not the stock side-effects of submitting a
        real inward)."""
        c = frappe.new_doc("Container")
        c.update({
            "container_no": cls.CONTAINER_NO, "lot_no": lot_no, "transaction_type": "VFY",
            "glue": cls.glue, "pulp": cls.pulp, "lusture": lusture, "grade": cls.grade, "fsc": cls.fsc,
            "merge_no": merge_no, "cross_section": "CS-1", "notes": f"notes for {lot_no}",
        })
        c.flags.ignore_validate = True
        c.flags.ignore_mandatory = True
        c.insert(ignore_permissions=True)
        frappe.db.set_value("Container", c.name, "docstatus", 1, update_modified=False)
        frappe.db.commit()
        return c

    @classmethod
    def tearDownClass(cls):
        for c in (cls.container_a, cls.container_b):
            frappe.db.set_value("Container", c.name, "docstatus", 0, update_modified=False)
            frappe.delete_doc("Container", c.name, force=1, ignore_permissions=True)
        frappe.db.commit()
        super().tearDownClass()

    def test_exact_container_and_lot_resolves_that_lot(self):
        spec = utilis.resolve_container_spec(self.CONTAINER_NO, "L1")
        self.assertEqual(spec["lusture"], self.lusture_a)
        self.assertEqual(spec["merge_no"], "MERGE-A")
        self.assertEqual(spec["notes"], "notes for L1")
        spec_b = utilis.resolve_container_spec(self.CONTAINER_NO, "L2")
        self.assertEqual(spec_b["lusture"], self.lusture_b)
        self.assertEqual(spec_b["merge_no"], "MERGE-B")

    def test_all_five_spec_fields_and_the_two_plain_ones_come_back(self):
        spec = utilis.resolve_container_spec(self.CONTAINER_NO, "L1")
        self.assertEqual(spec["glue"], self.glue)
        self.assertEqual(spec["pulp"], self.pulp)
        self.assertEqual(spec["grade"], self.grade)
        self.assertEqual(spec["fsc"], self.fsc)
        self.assertEqual(spec["cross_section"], "CS-1")

    def test_unmatched_lot_falls_back_to_container_and_transaction_type(self):
        spec = utilis.resolve_container_spec(self.CONTAINER_NO, "NO-SUCH-LOT", transaction_type="VFY")
        self.assertIn(spec["lusture"], (self.lusture_a, self.lusture_b), "falls back to *a* lot of this container")

    def test_container_no_alone_still_resolves_something(self):
        spec = utilis.resolve_container_spec(self.CONTAINER_NO)
        self.assertIn(spec["lusture"], (self.lusture_a, self.lusture_b))

    def test_unknown_container_is_none(self):
        self.assertIsNone(utilis.resolve_container_spec("MI1I130-NO-SUCH-CONTAINER"))

    def test_blank_container_no_is_none(self):
        self.assertIsNone(utilis.resolve_container_spec(""))
        self.assertIsNone(utilis.resolve_container_spec(None))

    def test_whitelisted_wrapper_delegates(self):
        spec = utilis.get_received_container_spec(self.CONTAINER_NO, "L1")
        self.assertEqual(spec["merge_no"], "MERGE-A")


class TestReceiptCarriesNormalizedSpec(FrappeTestCase):
    """End-to-end: a Send entry with a bare (unprefixed) grade -- the shape
    MI1-I107 leaves on an HTY batch pick -- still produces a Receive draft
    whose custom_grade is a real Item Specification docname, so
    make_receive_from_subcontractor().insert() does not hit Frappe's own
    Link validation error at the one write site this ticket does not touch
    (the pre-existing Send-side batch picker)."""

    def setUp(self):
        self.grade = _get_or_create_spec("Grade", "MI1I130 CARRY GRADE")
        self.source_name = self._insert_minimal_source()

    def tearDown(self):
        frappe.db.sql("DELETE FROM `tabStock Entry Detail` WHERE parent=%s", self.source_name)
        frappe.db.sql("DELETE FROM `tabStock Entry` WHERE name=%s", self.source_name)
        frappe.db.commit()

    def _insert_minimal_source(self):
        """Raw INSERT, bypassing controller validation (including the very
        Link check under test) -- exactly how test_subcontract_e2e_mi1_i50
        stands up a source without a full Subcontracting setup. Carries a
        BARE grade value, deliberately not a valid Item Specification
        docname on its own, to prove the copy in make_receive_from_
        subcontractor normalizes it rather than propagating it as-is."""
        name = f"_TEST-SE-SEND-{frappe.generate_hash(length=8)}"
        frappe.db.sql(
            """
            INSERT INTO `tabStock Entry`
                (name, owner, modified, modified_by, creation, docstatus,
                 purpose, stock_entry_type, posting_date, posting_time,
                 company, custom_subcontract_status, custom_overreceipt_tolerance_pct,
                 custom_grade, from_warehouse, to_warehouse)
            VALUES
                (%(name)s, 'Administrator', NOW(), 'Administrator', NOW(), 1,
                 'Send to Subcontractor', 'Send to Subcontractor', %(d)s, '00:00:00',
                 %(company)s, 'Open', 0, %(grade)s, %(src_wh)s, %(dst_wh)s)
            """,
            {"name": name, "d": nowdate(), "grade": "MI1I130 CARRY GRADE",
             "company": "Meher Creations", "src_wh": "Finished Goods - MC", "dst_wh": "SURAMYA YARN - MC"},
        )
        row = f"_TEST-SED-SEND-{frappe.generate_hash(length=8)}"
        frappe.db.sql(
            """
            INSERT INTO `tabStock Entry Detail`
                (name, owner, modified, modified_by, creation, docstatus,
                 parent, parenttype, parentfield, idx,
                 item_code, qty, transfer_qty, conversion_factor, uom, stock_uom,
                 s_warehouse, t_warehouse, custom_received_qty, custom_pending_qty)
            VALUES
                (%(row)s, 'Administrator', NOW(), 'Administrator', NOW(), 1,
                 %(parent)s, 'Stock Entry', 'items', 1,
                 '58D/8F', 10, 10, 1, 'Nos', 'Nos',
                 %(src_wh)s, %(dst_wh)s, 0, 10)
            """,
            {"row": row, "parent": name, "src_wh": "Finished Goods - MC", "dst_wh": "SURAMYA YARN - MC"},
        )
        frappe.db.commit()
        return name

    def test_carry_header_normalizes_the_bare_grade(self):
        src = frappe.get_doc("Stock Entry", self.source_name)
        self.assertEqual(src.custom_grade, "MI1I130 CARRY GRADE", "the raw insert bypassed validation, as intended")
        result = utilis.make_receive_from_subcontractor(self.source_name)
        receipt = frappe.get_doc("Stock Entry", result["name"])
        try:
            self.assertEqual(receipt.custom_grade, self.grade, "normalized to the real docname before insert()")
            self.assertTrue(frappe.db.exists("Item Specification", receipt.custom_grade))
        finally:
            frappe.delete_doc("Stock Entry", receipt.name, force=1, ignore_permissions=True)

    def test_source_code_normalizes_only_the_five_link_fields(self):
        import inspect
        src = inspect.getsource(utilis.make_receive_from_subcontractor)
        self.assertIn("if f in STOCK_ENTRY_SPEC_LINK_FIELDS:", src)
        self.assertIn("v = spec_link_value(STOCK_ENTRY_SPEC_LINK_FIELDS[f], v)", src)
