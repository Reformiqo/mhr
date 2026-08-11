"""MI1-I90 — Sales Order HTY parity fields.

Ports every HTY-related field that already exists on Delivery Note onto
Sales Order, grouped under a dedicated "HTY" tab so the existing VFY
layout is left exactly as it is.

Four header fields are deliberately NOT recreated because Sales Order
already carries them — re-adding would be a duplicate fieldname:

    transaction_type, custom_container_no, custom_lot_no, custom_cone

The HTY client code reads / writes those shared fields in place, so they
keep working for VFY and drive HTY at the same time.

Two Delivery Note fieldnames are renamed on Sales Order to follow the
`custom_` convention (DN's are legacy un-prefixed):

    DN `count`          -> SO `custom_count`
    DN `fetch_batches`  -> SO `custom_fetch_batches`

Idempotent: create_custom_fields(update=True) upserts, so re-running on
an already-patched site is a no-op.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

MODULE = "Mhr"

# The whole tab only renders in HTY mode — VFY users never see it.
HTY_ONLY = "eval:doc.transaction_type=='HTY'"


def _f(**kwargs):
	"""Every field this patch creates belongs to module Mhr so it exports
	with `bench export-fixtures --app mhr`."""
	kwargs.setdefault("module", MODULE)
	return kwargs


SALES_ORDER_FIELDS = [
	# ---- Tab ------------------------------------------------------------
	_f(
		fieldname="custom_hty_tab",
		label="HTY",
		fieldtype="Tab Break",
		insert_after="more_info",
		depends_on=HTY_ONLY,
	),
	# ---- Specification (mirrors DN's "Container Info" section) -----------
	_f(
		fieldname="custom_hty_spec_section",
		label="Specification",
		fieldtype="Section Break",
		insert_after="custom_hty_tab",
	),
	_f(
		fieldname="custom_batch",
		label="Batch",
		fieldtype="Link",
		options="Batch",
		insert_after="custom_hty_spec_section",
	),
	_f(
		fieldname="custom_denier",
		label="Denier",
		fieldtype="Link",
		options="Item",
		insert_after="custom_batch",
	),
	_f(
		fieldname="custom_glue",
		label="Glue",
		fieldtype="Data",
		insert_after="custom_denier",
	),
	_f(
		fieldname="custom_product",
		label="Product",
		fieldtype="Data",
		insert_after="custom_glue",
	),
	_f(
		fieldname="custom_pulp",
		label="Pulp",
		fieldtype="Data",
		insert_after="custom_product",
	),
	_f(
		fieldname="custom_type",
		label="Type",
		fieldtype="Data",
		insert_after="custom_pulp",
	),
	_f(
		fieldname="custom_hty_cb_1",
		fieldtype="Column Break",
		insert_after="custom_type",
	),
	_f(
		fieldname="custom_lusture",
		label="Lusture",
		fieldtype="Data",
		insert_after="custom_hty_cb_1",
	),
	_f(
		fieldname="custom_colour",
		label="Colour",
		fieldtype="Data",
		insert_after="custom_lusture",
	),
	_f(
		fieldname="custom_grade",
		label="Grade",
		fieldtype="Data",
		insert_after="custom_colour",
	),
	_f(
		fieldname="custom_fsc",
		label="FSC",
		fieldtype="Data",
		insert_after="custom_grade",
	),
	_f(
		fieldname="custom_merge_no",
		label="Merge No",
		fieldtype="Data",
		insert_after="custom_fsc",
	),
	_f(
		fieldname="custom_cross_section",
		label="Cross Section",
		fieldtype="Data",
		insert_after="custom_merge_no",
	),
	_f(
		fieldname="custom_hty_cb_2",
		fieldtype="Column Break",
		insert_after="custom_cross_section",
	),
	_f(
		fieldname="custom_supplier_batch_no",
		label="Supplier Batch No",
		fieldtype="Data",
		insert_after="custom_hty_cb_2",
	),
	_f(
		fieldname="custom_scan_batch_no",
		label="Scan Batch No",
		fieldtype="Data",
		options="Barcode",
		insert_after="custom_supplier_batch_no",
	),
	_f(
		fieldname="custom_warehouse",
		label="Location",
		fieldtype="Data",
		read_only=1,
		insert_after="custom_scan_batch_no",
	),
	_f(
		fieldname="custom_notes",
		label="Notes",
		fieldtype="Data",
		insert_after="custom_warehouse",
	),
	# ---- Fetch (mirrors DN's count / fetch_batches pair) -----------------
	_f(
		fieldname="custom_hty_fetch_section",
		label="Fetch Batches",
		fieldtype="Section Break",
		insert_after="custom_notes",
	),
	_f(
		fieldname="custom_count",
		label="Count",
		fieldtype="Int",
		insert_after="custom_hty_fetch_section",
		description="Max number of batch rows to pull in one Fetch Batches run.",
	),
	_f(
		fieldname="custom_fetch_batches",
		label="Fetch Batches",
		fieldtype="Check",
		insert_after="custom_count",
		depends_on="eval:doc.custom_count > 0",
	),
	_f(
		fieldname="custom_hty_cb_3",
		fieldtype="Column Break",
		insert_after="custom_fetch_batches",
	),
	_f(
		fieldname="custom_total_cone",
		label="Total Cone",
		fieldtype="Int",
		read_only=1,
		insert_after="custom_hty_cb_3",
	),
]


# Sales Order Item already has custom_batch_no / custom_lot_number /
# custom_container_number / custom_grade / custom_cone. These three are
# the HTY-only additions Delivery Note Item carries.
SALES_ORDER_ITEM_FIELDS = [
	_f(
		fieldname="custom_supplier_batch_no",
		label="Supplier Batch No",
		fieldtype="Data",
		insert_after="custom_batch_no",
	),
	_f(
		fieldname="custom_sr_no",
		label="Sr. No.",
		fieldtype="Data",
		insert_after="custom_supplier_batch_no",
	),
	_f(
		fieldname="custom_gross_weight",
		label="Gross Weight",
		fieldtype="Float",
		insert_after="custom_sr_no",
	),
]


# The Desk Client Script this app file supersedes. Its label swap, naming
# series switch and company-aware filters now live in
# public/js/sales_order_hty.js. Leaving both enabled would double-register
# the transaction_type handler and the set_query calls.
SUPERSEDED_CLIENT_SCRIPT = "MI1-I39 — Sales Order HTY Mode"


def execute():
	create_custom_fields(
		{
			"Sales Order": SALES_ORDER_FIELDS,
			"Sales Order Item": SALES_ORDER_ITEM_FIELDS,
		},
		update=True,
	)
	_disable_superseded_client_script()
	frappe.clear_cache(doctype="Sales Order")
	frappe.clear_cache(doctype="Sales Order Item")


def _disable_superseded_client_script():
	"""Disable, don't delete — the record stays visible in the Desk (and in
	the fixtures) as a breadcrumb, and the MI1-I39 tests that read its
	`script` body keep passing."""
	if not frappe.db.exists("Client Script", SUPERSEDED_CLIENT_SCRIPT):
		return
	if not frappe.db.get_value("Client Script", SUPERSEDED_CLIENT_SCRIPT, "enabled"):
		return
	frappe.db.set_value(
		"Client Script", SUPERSEDED_CLIENT_SCRIPT, "enabled", 0, update_modified=False
	)
