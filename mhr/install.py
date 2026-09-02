import json

import frappe

# Each target DocType's naming_series options must include these HTY-prefixed
# series. Created as a module=Mhr Property Setter so they ship via fixtures.
HTY_SERIES_BY_DOCTYPE = {
	"Sales Order": ["HTY-SO-.YYYY.-"],
	"Delivery Note": ["HTY-DN-.YYYY.-", "HTY-DN-RET-.YYYY.-"],
	"Stock Entry": ["HTY-STE-.YYYY.-"],
	"Delivery Trip": ["HTY-DT-.YYYY.-"],
}


# Transaction Type Link records the `transaction_type` Custom Field
# (default 'VFY') points at. Seeded by convert_transaction_type_to_link, but
# patches are marked done-without-running on a FRESH install (e.g. CI
# test_site) so the Link target stayed empty — every Stock Entry / Item
# opening-stock entry (default VFY) then failed link validation. Seed here
# too, since after_install runs on fresh installs.
SEED_TRANSACTION_TYPES = ("VFY", "HTY")


def after_install():
	ensure_transaction_types()
	ensure_hty_naming_series()


def after_migrate():
	ensure_transaction_types()
	ensure_hty_naming_series()
	repair_sales_order_hty_tab_position()


def ensure_transaction_types():
	"""Create the VFY / HTY Transaction Type records if missing. Idempotent;
	no-ops before the doctype exists."""
	if not frappe.db.exists("DocType", "Transaction Type"):
		return
	for name in SEED_TRANSACTION_TYPES:
		if not frappe.db.exists("Transaction Type", name):
			doc = frappe.new_doc("Transaction Type")
			doc.transaction_type_name = name
			doc.insert(ignore_permissions=True)


def ensure_hty_naming_series():
	"""Append the HTY naming-series prefixes to each target DocType.

	Appends to (never replaces) the existing naming_series options so standard
	and other custom series are preserved, then records a module=Mhr Property
	Setter so the customization exports with mhr's fixtures. Idempotent.
	"""
	for doctype, hty_series in HTY_SERIES_BY_DOCTYPE.items():
		if not frappe.db.exists("DocType", doctype):
			continue

		field = frappe.get_meta(doctype).get_field("naming_series")
		options = [o for o in ((field.options or "").splitlines() if field else []) if o.strip()]

		changed = False
		for series in hty_series:
			if series not in options:
				options.append(series)
				changed = True

		value = "\n".join(options)
		ps_name = frappe.db.get_value(
			"Property Setter",
			{"doc_type": doctype, "field_name": "naming_series", "property": "options"},
			"name",
		)

		if ps_name:
			ps = frappe.get_doc("Property Setter", ps_name)
			if ps.value != value or ps.module != "Mhr":
				ps.value = value
				ps.module = "Mhr"
				ps.save(ignore_permissions=True)
		elif changed:
			frappe.get_doc(
				{
					"doctype": "Property Setter",
					"doctype_or_field": "DocField",
					"doc_type": doctype,
					"field_name": "naming_series",
					"property": "options",
					"property_type": "Text",
					"value": value,
					"module": "Mhr",
				}
			).insert(ignore_permissions=True)

	frappe.clear_cache()


# ---------------------------------------------------------------------------
# Sales Order: keep the HTY tab holding its own fields.
#
# Customize Form writes a DocType-level `field_order` Property Setter, and when
# one exists frappe uses it verbatim and ignores every Custom Field's
# `insert_after` (frappe/model/meta.py :: Meta.sort_fields). On mhr.erpera.io
# that snapshot had `custom_hty_tab` pinned second from last — with only
# `connections_tab` after it, so the tab held nothing — while its 24 spec and
# fetch fields sat pinned at the very top of the form, above Series.
#
# A tab with no non-empty section is hidden (frappe/public/js/frappe/form/tab.js
# :: Tab.refresh), so the HTY tab vanished and its contents leaked into Details.
# Local benches, which have no such Property Setter, were unaffected — they fall
# back to `insert_after`, which is correct.
#
# The Property Setter is not ours: its `module` is empty, so mhr's fixtures
# (filtered on module = Mhr) neither export nor overwrite it, and no migrate
# ever repaired it. Rather than commit a 193-field layout of our own — which
# would force this site's field order onto every bench and go stale on each
# ERPNext upgrade — this moves only the HTY fields and leaves the rest of the
# order untouched, including whatever else was customised.
#
# Runs from after_migrate, not a patch: a patch runs once, and the next
# Customize Form save would break this again.
# ---------------------------------------------------------------------------

SALES_ORDER_FIELD_ORDER = "Sales Order-main-field_order"
HTY_TAB = "custom_hty_tab"
HTY_TAB_ANCHOR = "party_account_currency"


def repair_sales_order_hty_tab_position():
	"""Move the HTY tab and its fields back together. Idempotent; no-ops when
	no field_order Property Setter is pinning the layout."""
	if not frappe.db.exists("Property Setter", SALES_ORDER_FIELD_ORDER):
		# Nothing is overriding the layout, so insert_after already governs it.
		return

	setter = frappe.get_doc("Property Setter", SALES_ORDER_FIELD_ORDER)
	try:
		order = json.loads(setter.value or "[]")
	except ValueError:
		return

	if HTY_TAB not in order:
		return

	tab_fields = [f for f in _hty_tab_fieldnames() if f in order]
	if not tab_fields:
		return

	repaired = _order_with_hty_tab_intact(order, tab_fields)
	if repaired == order:
		# Already correct — the common case on every migrate after the first.
		return

	if sorted(repaired) != sorted(order):
		# Never silently drop or duplicate a field; leave the layout alone.
		frappe.log_error(
			title="MI1 Sales Order HTY tab repair skipped",
			message=(
				"Rebuilding the field order changed the field set, so it was not "
				f"written.\nbefore={len(order)} after={len(repaired)}"
			),
		)
		return

	setter.value = json.dumps(repaired)
	# save() rather than db.set_value: Property Setter.validate clears the
	# DocType cache, without which the form keeps serving the old layout.
	setter.save(ignore_permissions=True)

	frappe.logger().info(
		f"[MI1] Sales Order HTY tab repaired: moved {len(tab_fields) + 1} fields, "
		f"{len(order) - len(tab_fields) - 1} left untouched."
	)


def _hty_tab_fieldnames():
	"""The Custom Fields that hang off the HTY tab, in their declared order.

	Read from the insert_after chain rather than hardcoded, so a field added to
	the tab later is carried along without touching this function.
	"""
	by_anchor = {}
	for row in frappe.get_all(
		"Custom Field", filters={"dt": "Sales Order"}, fields=["fieldname", "insert_after"]
	):
		by_anchor.setdefault(row.insert_after, []).append(row.fieldname)

	ordered = []
	current = HTY_TAB
	while True:
		following = by_anchor.get(current)
		if not following:
			return ordered
		# The chain is linear: each field names the previous one. Guard anyway,
		# so a fork cannot spin this loop forever.
		nxt = following[0]
		if nxt in ordered:
			return ordered
		ordered.append(nxt)
		current = nxt


def _order_with_hty_tab_intact(order, tab_fields):
	"""`order` with the HTY tab moved to its anchor and its fields behind it."""
	moving = {HTY_TAB, *tab_fields}
	rest = [f for f in order if f not in moving]

	if HTY_TAB_ANCHOR in rest:
		at = rest.index(HTY_TAB_ANCHOR) + 1
	elif "connections_tab" in rest:
		at = rest.index("connections_tab")
	else:
		at = len(rest)

	# tab_fields keeps the order the Custom Field chain declares, so the tab's
	# own sections and column breaks stay in their designed sequence.
	return rest[:at] + [HTY_TAB] + tab_fields + rest[at:]
