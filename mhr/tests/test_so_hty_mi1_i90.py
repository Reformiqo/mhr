"""MI1-I90 — Sales Order HTY parity tests.

Covers the three things the requirement asks for:
  1. Delivery Note's HTY fields exist on Sales Order, under a new tab.
  2. The logic lives in the app (patch / hooks / public JS / python module),
     not in Desk Client Scripts or Server Scripts.
  3. Delivery Note is not affected.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from mhr.sales_order_hty import (
	HTY_SO_SERIES,
	_strip_label_prefix,
	get_company_hty_defaults,
	validate_hty_sales_order,
)


class _FakeRow:
	def __init__(self, **kwargs):
		self.__dict__.update(kwargs)


class _FakeSO:
	"""Minimal stand-in for a Sales Order doc — validate_hty_sales_order only
	ever reads via getattr, so a full doc (customer / item / company / FY)
	isn't needed to pin its behaviour."""

	def __init__(self, **kwargs):
		self.docstatus = 0
		self.transaction_type = "VFY"
		self.naming_series = "SAL-ORD-.YYYY.-"
		self.items = []
		self.custom_total_cone = 0
		self.__dict__.update(kwargs)


# ---------------------------------------------------------------------------
# 1. fields
# ---------------------------------------------------------------------------


class TestSalesOrderHTYFields(FrappeTestCase):
	"""Every HTY field Delivery Note carries must exist on Sales Order."""

	PORTED = [
		"custom_batch",
		"custom_denier",
		"custom_glue",
		"custom_product",
		"custom_pulp",
		"custom_type",
		"custom_lusture",
		"custom_colour",
		"custom_grade",
		"custom_fsc",
		"custom_merge_no",
		"custom_cross_section",
		"custom_supplier_batch_no",
		"custom_scan_batch_no",
		"custom_warehouse",
		"custom_notes",
		"custom_count",
		"custom_fetch_batches",
		"custom_total_cone",
	]

	# Already on Sales Order before MI1-I90 — reused in place rather than
	# duplicated, since a fieldname can only exist once per DocType.
	PRE_EXISTING = [
		"transaction_type",
		"custom_container_no",
		"custom_lot_no",
		"custom_cone",
	]

	def test_hty_tab_exists(self):
		tab = frappe.db.get_value(
			"Custom Field",
			{"dt": "Sales Order", "fieldname": "custom_hty_tab"},
			["fieldtype", "label", "module", "depends_on"],
			as_dict=True,
		)
		self.assertIsNotNone(tab, "Sales Order must have a custom_hty_tab field.")
		self.assertEqual(tab.fieldtype, "Tab Break", "The HTY group must be a Tab, not a Section.")
		self.assertEqual(tab.label, "HTY")
		self.assertEqual(tab.module, "Mhr", "Must be module=Mhr so it exports via fixtures.")
		self.assertIn(
			"HTY", tab.depends_on or "",
			"The HTY tab must only render in HTY mode so VFY layout is untouched.",
		)

	def test_all_ported_fields_present(self):
		meta = frappe.get_meta("Sales Order")
		for fieldname in self.PORTED:
			with self.subTest(fieldname=fieldname):
				self.assertIsNotNone(
					meta.get_field(fieldname),
					f"Sales Order is missing ported HTY field {fieldname!r}.",
				)

	def test_ported_fields_belong_to_mhr_module(self):
		for fieldname in self.PORTED:
			with self.subTest(fieldname=fieldname):
				module = frappe.db.get_value(
					"Custom Field", {"dt": "Sales Order", "fieldname": fieldname}, "module"
				)
				self.assertEqual(
					module, "Mhr",
					f"{fieldname} must be module=Mhr so `bench export-fixtures` picks it up.",
				)

	def test_pre_existing_fields_untouched(self):
		meta = frappe.get_meta("Sales Order")
		for fieldname in self.PRE_EXISTING:
			with self.subTest(fieldname=fieldname):
				self.assertIsNotNone(
					meta.get_field(fieldname),
					f"{fieldname} existed before MI1-I90 and must still exist.",
				)

	def test_item_level_fields_present(self):
		meta = frappe.get_meta("Sales Order Item")
		for fieldname in ("custom_supplier_batch_no", "custom_sr_no", "custom_gross_weight"):
			with self.subTest(fieldname=fieldname):
				self.assertIsNotNone(
					meta.get_field(fieldname),
					f"Sales Order Item is missing HTY field {fieldname!r}.",
				)


# ---------------------------------------------------------------------------
# 2. logic lives in the app
# ---------------------------------------------------------------------------


class TestSalesOrderHTYWiring(FrappeTestCase):
	def test_js_registered_via_doctype_js(self):
		hooks = frappe.get_hooks("doctype_js", app_name="mhr") or {}
		paths = hooks.get("Sales Order") or []
		if not isinstance(paths, list):
			paths = [paths]
		self.assertIn(
			"public/js/sales_order_hty.js", paths,
			"HTY logic must ship as an app JS file, not a Desk Client Script.",
		)
		self.assertEqual(
			paths, ["public/js/sales_order_hty.js"],
			"sales_order_hty.js is the only app JS on Sales Order. The former "
			"public/js/sales_order.js was deleted upstream because it duplicated "
			"the 'Sales Order Booking' Client Script — re-registering it here "
			"would bring those doubled handlers back.",
		)

	def test_validate_hook_registered(self):
		events = frappe.get_hooks("doc_events", app_name="mhr") or {}
		handlers = (events.get("Sales Order") or {}).get("validate") or []
		if not isinstance(handlers, list):
			handlers = [handlers]
		self.assertIn("mhr.sales_order_hty.validate_hty_sales_order", handlers)
		self.assertIn(
			"mhr.utilis.validate_so_available_qty", handlers,
			"The pre-existing Sales Order validate hook must be preserved.",
		)

	def test_no_server_script_for_sales_order(self):
		"""The requirement is explicit: logic goes in the custom app, not in
		Desk Client Script / Server Script records."""
		self.assertFalse(
			frappe.db.exists("Server Script", {"reference_doctype": "Sales Order"}),
			"Sales Order HTY logic must not be implemented as a Server Script.",
		)

	def test_superseded_client_script_is_disabled(self):
		"""public/js/sales_order_hty.js took over the label swap, naming-series
		switch and company-aware filters. Both running would double-register
		the transaction_type handler and the set_query calls."""
		name = "MI1-I39 — Sales Order HTY Mode"
		if not frappe.db.exists("Client Script", name):
			self.skipTest(f"{name} not present on this site.")
		self.assertEqual(
			frappe.db.get_value("Client Script", name, "enabled"), 0,
			f"{name} must be disabled — its logic now lives in the app.",
		)

	def test_superseded_client_script_is_disabled_in_the_fixture(self):
		"""The DB value above is not enough on its own.

		`bench migrate` runs patches first and calls sync_fixtures() afterwards
		(frappe/migrate.py :: post_schema_updates), so a fixture that still says
		`"enabled": 1` re-enables the script seconds after the patch disables it
		— every migrate, forever. That is exactly how the
		"Field not permitted in query: default_price_list" error came back after
		the first MI1-I90 deploy.
		"""
		import json

		path = frappe.get_app_path("mhr", "fixtures", "client_script.json")
		with open(path, encoding="utf-8") as f:
			records = json.load(f)

		match = [r for r in records if r.get("name") == "MI1-I39 — Sales Order HTY Mode"]
		self.assertEqual(len(match), 1, "Expected exactly one fixture record.")
		self.assertEqual(
			match[0].get("enabled"), 0,
			"The fixture is what migrate actually applies. Disabling this script "
			"only in the patch does not stick.",
		)

	def test_company_aware_behaviour_was_carried_over(self):
		"""Nothing the superseded Client Script did may be lost."""
		path = frappe.get_app_path("mhr", "public", "js", "sales_order_hty.js")
		with open(path, encoding="utf-8") as f:
			source = f.read()
		for needle in (
			"frm.set_query('set_warehouse'",       # FRD §SO 2 — header warehouse by company
			"frm.set_query('warehouse', 'items'",  # FRD §SO 2 — item warehouse by company
			"get_company_hty_defaults",            # FRD §SO 4/5 — price list + cost center
		):
			with self.subTest(needle=needle):
				self.assertIn(needle, source)

	def test_client_never_queries_company_default_price_list(self):
		"""Regression guard for the bug that broke every HTY Sales Order:
		Company has no default_price_list field, so a client-side get_value
		asking for it throws 'Field not permitted in query'."""
		path = frappe.get_app_path("mhr", "public", "js", "sales_order_hty.js")
		with open(path, encoding="utf-8") as f:
			source = f.read()
		self.assertNotIn(
			"get_value('Company'", source,
			"Company defaults must be resolved server-side, not with a direct "
			"client-side field query.",
		)


# ---------------------------------------------------------------------------
# 3. company defaults (the default_price_list fix)
# ---------------------------------------------------------------------------


class TestCompanyHTYDefaults(FrappeTestCase):
	def setUp(self):
		self.company = frappe.db.get_value("Company", {}, "name")
		if not self.company:
			self.skipTest("No Company on this site.")

	def test_company_has_no_default_price_list_field(self):
		"""Pins the premise of the fix. If ERPNext ever adds this field, the
		endpoint already prefers it and this test should be updated."""
		self.assertIsNone(
			frappe.get_meta("Company").get_field("default_price_list"),
			"Company gained a default_price_list field — revisit "
			"get_company_hty_defaults.",
		)

	def test_returns_cost_center_from_company(self):
		result = get_company_hty_defaults(self.company)
		self.assertEqual(
			result.get("cost_center"),
			frappe.db.get_value("Company", self.company, "cost_center"),
		)

	def test_never_raises_for_any_company(self):
		"""The whole point: this must not throw the way the client query did."""
		for company in frappe.get_all("Company", pluck="name"):
			with self.subTest(company=company):
				self.assertIsInstance(get_company_hty_defaults(company), dict)

	def test_falls_back_to_selling_settings(self):
		result = get_company_hty_defaults(self.company)
		default = frappe.db.get_single_value("Selling Settings", "selling_price_list")
		if not default:
			self.skipTest("Selling Settings has no selling_price_list configured.")
		self.assertEqual(result.get("selling_price_list"), default)

	def test_customer_default_price_list_wins_over_settings(self):
		customer = frappe.db.get_value(
			"Customer", {"default_price_list": ["!=", ""]}, ["name", "default_price_list"], as_dict=True
		)
		if not customer:
			self.skipTest("No Customer with a default_price_list on this site.")
		result = get_company_hty_defaults(self.company, customer.name)
		self.assertEqual(result.get("selling_price_list"), customer.default_price_list)

	def test_blank_company_returns_empty(self):
		self.assertEqual(get_company_hty_defaults(None), {})


# ---------------------------------------------------------------------------
# 4. validate hook behaviour
# ---------------------------------------------------------------------------


class TestValidateHTYSalesOrder(FrappeTestCase):
	def test_vfy_order_is_untouched(self):
		doc = _FakeSO(transaction_type="VFY", naming_series="SAL-ORD-.YYYY.-")
		validate_hty_sales_order(doc)
		self.assertEqual(doc.naming_series, "SAL-ORD-.YYYY.-")
		self.assertEqual(doc.custom_total_cone, 0)

	def test_hty_order_gets_hty_series(self):
		doc = _FakeSO(transaction_type="HTY", naming_series="SAL-ORD-.YYYY.-")
		validate_hty_sales_order(doc)
		self.assertEqual(doc.naming_series, HTY_SO_SERIES)

	def test_hty_series_already_set_is_kept(self):
		doc = _FakeSO(transaction_type="HTY", naming_series="HTY-SO-.YYYY.-")
		validate_hty_sales_order(doc)
		self.assertEqual(doc.naming_series, "HTY-SO-.YYYY.-")

	def test_submitted_doc_is_skipped(self):
		doc = _FakeSO(transaction_type="HTY", naming_series="SAL-ORD-.YYYY.-", docstatus=1)
		validate_hty_sales_order(doc)
		self.assertEqual(
			doc.naming_series, "SAL-ORD-.YYYY.-",
			"A submitted order must never have its series rewritten.",
		)

	def test_total_cone_is_recomputed(self):
		doc = _FakeSO(
			transaction_type="HTY",
			custom_total_cone=999,
			items=[
				_FakeRow(custom_cone=4, custom_batch_no=None),
				_FakeRow(custom_cone=6, custom_batch_no=None),
			],
		)
		validate_hty_sales_order(doc)
		self.assertEqual(doc.custom_total_cone, 10)

	def test_vfy_batch_rejected_on_hty_order(self):
		vfy_batch = frappe.db.get_value("Batch", {"custom_transaction_type": "VFY"}, "name")
		if not vfy_batch:
			self.skipTest("No VFY batch on this site to exercise the guard.")
		doc = _FakeSO(
			transaction_type="HTY",
			items=[_FakeRow(custom_cone=1, custom_batch_no=vfy_batch)],
		)
		with self.assertRaises(frappe.ValidationError):
			validate_hty_sales_order(doc)

	def test_hty_batch_accepted_on_hty_order(self):
		hty_batch = frappe.db.get_value("Batch", {"custom_transaction_type": "HTY"}, "name")
		if not hty_batch:
			self.skipTest("No HTY batch on this site to exercise the guard.")
		doc = _FakeSO(
			transaction_type="HTY",
			items=[_FakeRow(custom_cone=3, custom_batch_no=hty_batch)],
		)
		validate_hty_sales_order(doc)
		self.assertEqual(doc.custom_total_cone, 3)


# ---------------------------------------------------------------------------
# 5. prefix stripping stays consistent with the popup
# ---------------------------------------------------------------------------


class TestStripLabelPrefix(FrappeTestCase):
	def test_single_hyphen(self):
		self.assertEqual(_strip_label_prefix("Product-HTY"), "HTY")
		self.assertEqual(_strip_label_prefix("Type-PALLET"), "PALLET")

	def test_value_with_spaces(self):
		self.assertEqual(_strip_label_prefix("Colour-RAW WHITE"), "RAW WHITE")

	def test_multi_hyphen_splits_on_first(self):
		# mhr.utilis.strip_prefix splits on the LAST hyphen and would return
		# 'WHITE' here. The popup shows 'OFF-WHITE', so the stored header
		# value has to match that.
		self.assertEqual(_strip_label_prefix("Colour-OFF-WHITE"), "OFF-WHITE")

	def test_no_hyphen_is_unchanged(self):
		self.assertEqual(_strip_label_prefix("Wood"), "Wood")

	def test_empty(self):
		self.assertEqual(_strip_label_prefix(""), "")
		self.assertEqual(_strip_label_prefix(None), "")


# ---------------------------------------------------------------------------
# 6. Delivery Note must be unaffected
# ---------------------------------------------------------------------------


class TestDeliveryNoteUnaffected(FrappeTestCase):
	DN_HTY_FIELDS = [
		"transaction_type",
		"custom_container_no",
		"custom_lot_no",
		"custom_cone",
		"custom_denier",
		"custom_product",
		"custom_type",
		"custom_colour",
		"custom_total_cone",
	]

	def test_delivery_note_fields_still_present(self):
		meta = frappe.get_meta("Delivery Note")
		for fieldname in self.DN_HTY_FIELDS:
			with self.subTest(fieldname=fieldname):
				self.assertIsNotNone(
					meta.get_field(fieldname),
					f"MI1-I90 must not remove Delivery Note field {fieldname!r}.",
				)

	def test_delivery_note_hty_client_script_still_enabled(self):
		name = "MI1-I39 — Delivery Note HTY Mode"
		if not frappe.db.exists("Client Script", name):
			self.skipTest(f"{name} not present on this site.")
		self.assertEqual(
			frappe.db.get_value("Client Script", name, "enabled"), 1,
			"The Delivery Note HTY Client Script must stay enabled.",
		)

	def test_delivery_note_validate_hooks_intact(self):
		events = frappe.get_hooks("doc_events", app_name="mhr") or {}
		handlers = (events.get("Delivery Note") or {}).get("validate") or []
		for expected in (
			"mhr.utilis.set_delivery_note_user",
			"mhr.utilis.set_return_cone_from_original",
			"mhr.utilis.calculate_delivery_note_totals",
			"mhr.utilis.fetch_notes_from_container",
		):
			with self.subTest(handler=expected):
				self.assertIn(expected, handlers)

	def test_no_sales_order_hook_leaked_onto_delivery_note(self):
		events = frappe.get_hooks("doc_events", app_name="mhr") or {}
		handlers = (events.get("Delivery Note") or {}).get("validate") or []
		self.assertNotIn("mhr.sales_order_hty.validate_hty_sales_order", handlers)
