"""MI1-I108 — the Sales Order -> Delivery Note override is called by POSITION.

MI1-I90 wrapped ERPNext's `make_delivery_note` through
`override_whitelisted_methods`. The wrapper's tail was `**kwargs`, which reads
as "accept anything" but accepts nothing by position. Frappe calls the
override positionally, and the two entry points disagree on how many
arguments that is (frappe/model/mapper.py, v15):

    make_mapped_doc()   Sales Order > Create > Delivery Note
                        return method(source_name)                    1 arg

    map_docs()          Delivery Note > Get Items From > Sales Order
                        _args = (src, target_doc, json.loads(args))
                                    if args else (src, target_doc)
                        target_doc = method(*_args)                   3 args

The Get Items From dialog always sends args — `{"customer": ...,
"allow_child_item_selection": 0, "filtered_children": []}` — so that path
always calls with three positionals. Hence, from production:

    TypeError: make_delivery_note() takes from 1 to 2 positional arguments
    but 3 were given

Only the Create button was exercised in MI1-I90, which is why the second
entry point went unnoticed. These tests pin the signature to the function
being overridden, and pin the call shapes both entry points actually use, so
neither a local edit nor an upstream signature change can reintroduce this
silently.
"""

import inspect
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from mhr import sales_order_to_delivery_note as so2dn


def _upstream():
	from erpnext.selling.doctype.sales_order.sales_order import make_delivery_note

	return make_delivery_note


class TestOverrideSignatureMatchesUpstream(FrappeTestCase):
	"""An override that does not accept the caller's arguments is a 500."""

	def test_signature_is_identical_to_the_overridden_function(self):
		self.assertEqual(
			str(inspect.signature(so2dn.make_delivery_note)),
			str(inspect.signature(_upstream())),
			"The wrapper's signature drifted from ERPNext's. frappe calls the "
			"override positionally, so any drift is a TypeError on the button, "
			"not a mismatched keyword.",
		)

	def test_accepts_the_three_positionals_map_docs_sends(self):
		params = [
			p
			for p in inspect.signature(so2dn.make_delivery_note).parameters.values()
			if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
		]
		self.assertGreaterEqual(
			len(params),
			3,
			"map_docs() calls the override with (source_name, target_doc, args). "
			f"This one takes {len(params)} by position — MI1-I108 all over again.",
		)

	def test_no_var_keyword_tail(self):
		"""`**kwargs` is what made the old signature look wide and be narrow."""
		kinds = [p.kind for p in inspect.signature(so2dn.make_delivery_note).parameters.values()]
		self.assertNotIn(
			inspect.Parameter.VAR_KEYWORD,
			kinds,
			"A **kwargs tail absorbs nothing by position; spell the third "
			"parameter out, exactly as ERPNext does.",
		)

	def test_third_parameter_is_named_as_erpnext_names_it(self):
		"""Callers that pass it by keyword pass `kwargs=` (pick list, stock
		reservation). The name is part of the contract, not decoration."""
		ours = list(inspect.signature(so2dn.make_delivery_note).parameters)
		theirs = list(inspect.signature(_upstream()).parameters)
		self.assertEqual(ours, theirs)


class TestBothEntryPointsCanCallIt(FrappeTestCase):
	"""Exercises the call shapes frappe uses, without needing a saved Sales
	Order — ERPNext's mapping is stubbed, so what is under test is purely the
	wrapper's argument handling."""

	def _capture(self):
		seen = {}

		def fake(source_name, target_doc=None, kwargs=None):
			seen["call"] = (source_name, target_doc, kwargs)
			# None short-circuits carry_hty_details, which is deliberate: the
			# HTY carry-over has its own tests in MI1-I90.
			return None

		return seen, fake

	def _patched(self, fake):
		import erpnext.selling.doctype.sales_order.sales_order as so_module

		return patch.object(so_module, "make_delivery_note", fake)

	def test_one_positional__create_button(self):
		"""make_mapped_doc(): method(source_name)."""
		seen, fake = self._capture()
		with self._patched(fake):
			so2dn.make_delivery_note("HTY-SO-2026-00001")
		self.assertEqual(seen["call"], ("HTY-SO-2026-00001", None, None))

	def test_two_positionals__map_docs_without_args(self):
		seen, fake = self._capture()
		target = {"doctype": "Delivery Note"}
		with self._patched(fake):
			so2dn.make_delivery_note("HTY-SO-2026-00001", target)
		self.assertEqual(seen["call"], ("HTY-SO-2026-00001", target, None))

	def test_three_positionals__get_items_from_sales_order(self):
		"""The exact shape that raised the MI1-I108 TypeError."""
		seen, fake = self._capture()
		target = {"doctype": "Delivery Note", "transaction_type": "HTY"}
		args = {
			"customer": "Eagle Silk Mills Pvt Ltd",
			"allow_child_item_selection": 0,
			"filtered_children": [],
		}
		with self._patched(fake):
			so2dn.make_delivery_note("HTY-SO-2026-00001", target, args)
		self.assertEqual(seen["call"], ("HTY-SO-2026-00001", target, args))

	def test_kwargs_reaches_erpnext_unchanged(self):
		"""ERPNext reads filtered_children out of it to honour the row
		selection in the dialog. Dropping or rewrapping it would silently
		deliver every line of the Sales Order."""
		seen, fake = self._capture()
		args = {"filtered_children": ["abc123"], "customer": "X"}
		with self._patched(fake):
			so2dn.make_delivery_note("HTY-SO-2026-00001", None, args)
		self.assertIs(seen["call"][2], args)

	def test_keyword_form_still_works(self):
		seen, fake = self._capture()
		with self._patched(fake):
			so2dn.make_delivery_note("HTY-SO-2026-00001", kwargs={"customer": "X"})
		self.assertEqual(seen["call"], ("HTY-SO-2026-00001", None, {"customer": "X"}))


class TestMapDocsContractStillHolds(FrappeTestCase):
	"""If frappe ever changes how it invokes the override, the tests above go
	green while production breaks. Pin the caller too."""

	def test_map_docs_passes_three_positionals_when_args_are_present(self):
		from frappe.model import mapper

		source = inspect.getsource(mapper.map_docs)
		self.assertIn("method(*_args)", source)
		# Deliberately not pinning how `args` is decoded — v15 and v16 use
		# json.loads, develop uses frappe.parse_json. What matters is that the
		# decoded value goes in as the THIRD positional.
		self.assertIn("(src, target_doc,", source)

	def test_override_is_resolved_before_the_call(self):
		"""map_docs must route through override_whitelisted_method, otherwise
		Get Items From bypasses this module entirely and the HTY fields land
		empty rather than erroring — a silent failure, which is worse."""
		from frappe.model import mapper

		self.assertIn(
			"override_whitelisted_method",
			inspect.getsource(mapper.map_docs),
		)

	def test_the_override_is_whitelisted(self):
		"""map_docs raises PermissionError for anything not in
		frappe.whitelisted, and the resolved target is what it checks."""
		target = frappe.get_attr(
			frappe.get_hooks("override_whitelisted_methods")[
				"erpnext.selling.doctype.sales_order.sales_order.make_delivery_note"
			][-1]
		)
		self.assertIn(target, frappe.whitelisted)
