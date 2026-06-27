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
