"""The Sales Order HTY tab vanished on prod while its fields leaked into Details.

Customize Form writes a DocType-level `field_order` Property Setter, and when
one exists frappe uses it verbatim and ignores every Custom Field's
`insert_after` (frappe/model/meta.py :: Meta.sort_fields). On mhr.erpera.io that
snapshot had `custom_hty_tab` pinned second from last — only `connections_tab`
after it, so the tab held nothing — and its 24 spec/fetch fields pinned at the
very top of the form, above Series.

A tab with no non-empty section is hidden (frappe/public/js/frappe/form/tab.js
:: Tab.refresh), so the tab disappeared and its contents showed in Details.

Three things this must get right, and each has a test below:

  the fix       the HTY tab and its fields end up together, at the tab's anchor
  the restraint every other field keeps its position, customisations included
  the safety    it never runs when no Property Setter is pinning the layout,
				never changes the field set, and is a no-op once correct
"""

import json

import frappe
from frappe.tests.utils import FrappeTestCase

from mhr import install

# The layout as it stood on mhr.erpera.io, trimmed to the shape that matters:
# the HTY block hoisted to the top, the tab stranded at the end, and one real
# customisation in between (sales_team moved above currency_and_price_list).
HTY_BLOCK = [
	"custom_hty_spec_section",
	"custom_batch",
	"custom_hty_cb_1",
	"custom_colour",
	"custom_hty_cb_2",
	"custom_supplier_batch_no",
	"custom_hty_fetch_section",
	"custom_count",
	"custom_total_cone",
]
BROKEN_ORDER = [
	"custom_container_no",
	"custom_lot_no",
	*HTY_BLOCK,
	"customer_section",
	"naming_series",
	"transaction_type",
	"section_break1",
	"sales_team",
	"currency_and_price_list",
	"more_info",
	"party_account_currency",
	"custom_hty_tab",
	"connections_tab",
]


def _repair(order, tab_fields=None):
	return install._order_with_hty_tab_intact(order, tab_fields or HTY_BLOCK)


class TestTheTabEndsUpHoldingItsFields(FrappeTestCase):
	def test_the_block_follows_the_tab_with_nothing_in_between(self):
		"""This is the whole bug: an empty tab is a hidden tab."""
		out = _repair(BROKEN_ORDER)
		at = out.index(install.HTY_TAB)
		self.assertEqual(out[at + 1 : at + 1 + len(HTY_BLOCK)], HTY_BLOCK)

	def test_the_tab_sits_at_the_anchor_its_custom_field_declares(self):
		out = _repair(BROKEN_ORDER)
		self.assertEqual(
			out[out.index(install.HTY_TAB_ANCHOR) + 1], install.HTY_TAB
		)

	def test_the_hty_fields_no_longer_precede_the_first_section(self):
		out = _repair(BROKEN_ORDER)
		for fieldname in HTY_BLOCK:
			self.assertGreater(out.index(fieldname), out.index("customer_section"))

	def test_the_block_keeps_its_declared_internal_order(self):
		"""Sections and column breaks must stay in their designed sequence."""
		out = _repair(BROKEN_ORDER)
		self.assertEqual([f for f in out if f in set(HTY_BLOCK)], HTY_BLOCK)


class TestEverythingElseIsLeftAlone(FrappeTestCase):
	def test_no_field_is_added_or_dropped(self):
		self.assertEqual(sorted(_repair(BROKEN_ORDER)), sorted(BROKEN_ORDER))

	def test_only_the_tab_and_its_own_fields_move(self):
		out = _repair(BROKEN_ORDER)
		moving = {install.HTY_TAB, *HTY_BLOCK}
		self.assertEqual(
			[f for f in out if f not in moving],
			[f for f in BROKEN_ORDER if f not in moving],
		)

	def test_an_unrelated_customisation_survives(self):
		"""mhr.erpera.io has sales_team moved above currency_and_price_list.
		Deleting the Property Setter would have lost that; this keeps it."""
		out = _repair(BROKEN_ORDER)
		self.assertLess(out.index("sales_team"), out.index("currency_and_price_list"))

	def test_it_lands_before_connections_tab_when_the_anchor_is_gone(self):
		"""A future ERPNext could drop party_account_currency. The tab must
		still not be the last thing on the form, or it is empty again."""
		order = [f for f in BROKEN_ORDER if f != install.HTY_TAB_ANCHOR]
		out = _repair(order)
		self.assertEqual(out[out.index(install.HTY_TAB) + 1], HTY_BLOCK[0])
		self.assertLess(out.index(install.HTY_TAB), out.index("connections_tab"))


class TestItIsSafeToRunOnEveryMigrate(FrappeTestCase):
	def test_a_second_run_changes_nothing(self):
		once = _repair(BROKEN_ORDER)
		self.assertEqual(_repair(once), once)

	def test_it_is_wired_into_after_migrate(self):
		import inspect

		self.assertIn(
			"repair_sales_order_hty_tab_position()",
			inspect.getsource(install.after_migrate),
		)

	def test_it_does_nothing_without_a_field_order_property_setter(self):
		"""Local benches have none — frappe uses insert_after there and is
		already correct, so this must not create or touch anything."""
		if frappe.db.exists("Property Setter", install.SALES_ORDER_FIELD_ORDER):
			self.skipTest("This site pins Sales Order's field order.")
		self.assertIsNone(install.repair_sales_order_hty_tab_position())

	def test_the_field_set_check_guards_the_write(self):
		"""Rebuilding must never drop or duplicate a field; if it somehow did,
		the layout is left alone rather than half-written."""
		import inspect

		source = inspect.getsource(install.repair_sales_order_hty_tab_position)
		self.assertIn("if sorted(repaired) != sorted(order):", source)
		guard = source.index("if sorted(repaired) != sorted(order):")
		self.assertLess(guard, source.index("setter.save("))


class TestTheTabFieldsAreReadFromTheCustomFieldChain(FrappeTestCase):
	"""Hardcoding the list would go stale the moment a field joins the tab."""

	def test_it_walks_insert_after_from_the_tab(self):
		fieldnames = install._hty_tab_fieldnames()
		if not fieldnames:
			self.skipTest("Sales Order HTY custom fields are not on this site.")

		self.assertEqual(fieldnames[0], "custom_hty_spec_section")
		self.assertIn("custom_total_cone", fieldnames)

		# Every one of them really does hang off the chain, not off some other
		# part of the form.
		anchors = dict(
			frappe.get_all(
				"Custom Field",
				filters={"dt": "Sales Order", "fieldname": ("in", fieldnames)},
				fields=["fieldname", "insert_after"],
				as_list=True,
			)
		)
		expected = [install.HTY_TAB, *fieldnames[:-1]]
		self.assertEqual([anchors[f] for f in fieldnames], expected)

	def test_the_fixture_still_declares_the_tab_and_its_anchor(self):
		path = frappe.get_app_path("mhr", "fixtures", "custom_field.json")
		with open(path, encoding="utf-8") as f:
			records = json.load(f)
		tab = next(
			r for r in records if r.get("name") == "Sales Order-custom_hty_tab"
		)
		self.assertEqual(tab["fieldtype"], "Tab Break")
		self.assertEqual(tab["insert_after"], install.HTY_TAB_ANCHOR)
