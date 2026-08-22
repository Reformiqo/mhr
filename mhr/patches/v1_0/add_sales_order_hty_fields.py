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

Picking a Batch fills the form in, and that is metadata rather than code:
on Delivery Note twelve fields declare `fetch_from = custom_batch.<x>`, so
frappe populates them as soon as the Batch link resolves. No Client Script
is involved — which is why the first cut of this patch created the fields
but left the form inert. The same nine declarations are made here, plus
three on fields Sales Order already had (see
SHARED_FIELDS_GAINING_FETCH_FROM).

`custom_product`, `custom_type`, `custom_colour` and `custom_cross_section`
are deliberately NOT fetched from the Batch — they belong to the Container,
and sales_order_hty.js resolves them through
mhr.sales_order_hty.get_container_spec_for_batch. A container_no lookup is
ambiguous (one number maps to many Container docs), so it has to go via the
batch's own Batch Items row.

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
		fetch_from="custom_batch.item",
		fetch_if_empty=0,
		label="Denier",
		fieldtype="Link",
		options="Item",
		insert_after="custom_batch",
	),
	_f(
		fieldname="custom_glue",
		fetch_from="custom_batch.custom_glue",
		fetch_if_empty=0,
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
		fetch_from="custom_batch.custom_pulp",
		fetch_if_empty=0,
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
		fetch_from="custom_batch.custom_lusture",
		fetch_if_empty=0,
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
		fetch_from="custom_batch.custom_grade",
		fetch_if_empty=0,
		label="Grade",
		fieldtype="Data",
		insert_after="custom_colour",
	),
	_f(
		fieldname="custom_fsc",
		fetch_from="custom_batch.custom_fsc",
		fetch_if_empty=0,
		label="FSC",
		fieldtype="Data",
		insert_after="custom_grade",
	),
	_f(
		fieldname="custom_merge_no",
		fetch_from="custom_batch.custom_merge_no",
		fetch_if_empty=0,
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
		fetch_from="custom_batch.custom_warehouse",
		fetch_if_empty=0,
		label="Location",
		fieldtype="Data",
		read_only=1,
		insert_after="custom_scan_batch_no",
	),
	_f(
		fieldname="custom_notes",
		fetch_from="custom_batch.custom_notes",
		fetch_if_empty=0,
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
	# The two fields the cone -> qty rule runs on. Same fieldnames as
	# Delivery Note Item, so Create > Delivery Note carries them across and
	# neither form re-derives a qty the other already settled.
	#
	# Hidden, exactly as on Delivery Note: they are bookkeeping for the
	# calculation, not something a user fills in.
	_f(
		fieldname="custom_cone_copy",
		label="Cone Copy",
		fieldtype="Data",
		hidden=1,
		no_copy=0,
		insert_after="custom_gross_weight",
		description="The cone this row's qty was derived from. qty scales by "
		"custom_cone / custom_cone_copy.",
	),
	_f(
		fieldname="custom_qty_manual_edit",
		label="Qty Manual Edit",
		fieldtype="Check",
		hidden=1,
		no_copy=0,
		insert_after="custom_cone_copy",
		description="Set when the user types a qty. Blocks the cone -> qty "
		"recalculation on this row, here and on the Delivery Note made from it.",
	),
]


# Three fields Sales Order already had, gaining only the Delivery Note's
# `fetch_from`. This is what makes picking a Batch fill the form in — on
# Delivery Note that behaviour is not script at all, it is field metadata:
# twelve of its fields declare `fetch_from = custom_batch.<something>`, so
# frappe populates them the moment the Batch link resolves.
#
# NO `insert_after` here on purpose. These three sit on the Details tab and
# are what the VFY "Sales Order Booking" flow writes to; moving them would
# rearrange a form VFY users depend on.
#
# Safe for VFY: frappe only fetches when the source link has a value
# (frappe/model/base_document.py :: get_invalid_links -> `if docname:`), and
# custom_batch lives on the HTY tab, which VFY never renders. An empty
# custom_batch fetches nothing and clears nothing.
SHARED_FIELDS_GAINING_FETCH_FROM = [
	_f(
		fieldname="custom_container_no",
		label="Container No",
		fieldtype="Data",
		fetch_from="custom_batch.custom_container_no",
		fetch_if_empty=0,
	),
	_f(
		fieldname="custom_lot_no",
		label="Lot No",
		fieldtype="Data",
		fetch_from="custom_batch.custom_lot_no",
		fetch_if_empty=0,
	),
	_f(
		fieldname="custom_cone",
		label="Cone",
		fieldtype="Int",
		fetch_from="custom_batch.custom_cone",
		fetch_if_empty=0,
	),
]


# The Desk Client Script this app file supersedes. Its label swap, naming
# series switch and company-aware filters now live in
# public/js/sales_order_hty.js. Leaving both enabled would double-register
# the transaction_type handler and the set_query calls — and worse, that
# script's `company` handler reads Company.default_price_list, a field
# ERPNext does not have, so it throws "Field not permitted in query:
# default_price_list" the moment transaction_type is set to HTY.
#
# The REAL switch is `"enabled": 0` in mhr/fixtures/client_script.json.
# `bench migrate` runs patches inside run_schema_updates() and only then
# calls post_schema_updates() -> sync_fixtures(), so a fixture still saying
# `"enabled": 1` re-enables the script seconds after this patch disables it
# — on every single migrate. The DB write below is kept as a safety net for
# sites that do not re-sync fixtures, and is idempotent.
SUPERSEDED_CLIENT_SCRIPT = "MI1-I39 — Sales Order HTY Mode"


def execute():
	create_custom_fields(
		{
			"Sales Order": SALES_ORDER_FIELDS + SHARED_FIELDS_GAINING_FETCH_FROM,
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
