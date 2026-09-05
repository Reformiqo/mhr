"""MI1-I90 / MI1-I117 — Sales Order -> Delivery Note.

The order flow is: raise a Sales Order, then turn it into a Delivery Note
(the "Delivery Challan" of the shop floor — there is no Delivery Challan
DocType; DELIVERY CHALLAN is a report over Delivery Notes).

MI1-I90 wrote this for HTY and gated the item work on it. MI1-I117 removed
that gate: the fieldname mismatch below is a property of the two DocTypes,
not of the mode, and a VFY note was arriving with no container, lot or batch
on its rows. That broke the reports as well as the data — see
carry_sales_order_details.

ERPNext already ships the mapping
(`erpnext.selling.doctype.sales_order.sales_order.make_delivery_note`) and
two entry points into it: Sales Order > Create > Delivery Note, and, from
the other side, Delivery Note > Get Items From > Sales Order. This module
wraps the mapping via `override_whitelisted_methods` — so both entry points
route here — and fills the two gaps that stop the HTY specification from
arriving on the Delivery Note. Everything here is a no-op unless the Sales
Order is HTY, so the VFY flow and every Delivery Note created directly are
untouched.


Gap 1 — the item table uses different fieldnames on the two DocTypes
--------------------------------------------------------------------
frappe's mapper copies a field only when the SAME fieldname exists on both
sides (frappe/model/mapper.py :: map_fields). Sales Order Item predates the
HTY work and names its columns differently from Delivery Note Item:

    Sales Order Item              Delivery Note Item
    custom_container_number  ->   custom_container_no
    custom_lot_number        ->   custom_lot_no
    custom_batch_no          ->   batch_no

Without ITEM_FIELD_MAP those three land empty, silently — the Delivery Note
saves fine, it just has no container, lot or batch on its rows.

`custom_supplier_batch_no` shares its name and so is copied, but only when
the Sales Order row carries one, and the Sales Order Booking flow never sets
it. Delivery Note Item declares no `fetch_from` for it either, so it is
resolved from the row's Batch instead — see _fill_supplier_batch_numbers.

The header needs no such table: the MI1-I90 patch creates the Sales Order
HTY fields under the Delivery Note's own fieldnames, and no Delivery Note
custom field carries `no_copy`, so the mapper copies the whole spec block
by itself. That cuts both ways — `transaction_type` has the same fieldname
on both sides and no `no_copy`, so the Sales Order's mode is written onto
the Delivery Note, replacing whatever the user had selected there.


Gap 2 — 'Cone Qty Calcuation' would overwrite the ordered qty
-------------------------------------------------------------
That Client Script's `before_save` recomputes, for every row that has a
batch and a cone,

    qty = (Batch.batch_qty * custom_cone) / custom_cone_copy

On a Sales Order-sourced row `custom_cone_copy` starts empty, so the script
sets it equal to `custom_cone`, the ratio becomes 1, and the row's qty is
replaced by the batch's FULL quantity. A Sales Order for 10 boxes out of a
25-box batch would deliver 25.

The script already honours a per-row opt-out, `custom_qty_manual_edit`, and
resets it to 0 the moment the user edits that row's cone. Setting the flag
on mapped rows is therefore enough: the ordered qty survives, and a user who
changes the cone on the Delivery Note gets the recalculation back. No
Client Script is modified — the existing Delivery Note behaviour and its
validations stay exactly as they are.


Not carried on purpose
----------------------
`count` / `fetch_batches` (Sales Order names them `custom_count` /
`custom_fetch_batches`, so the mapper cannot copy them anyway). They are
fetch CONTROLS, not data: arriving on the Delivery Note with fetch_batches
ticked would kick off a bulk batch fetch and append rows on top of the ones
just mapped from the Sales Order.

`naming_series` carries `no_copy = 1` on both DocTypes, so the mapper
already refuses it and the Delivery Note keeps its own company/mode series
(MC-HTY-ST-DN, MI-HTY-ST-DN, MF-HT-ST-DN, ...). Pinned by a test, because
the Sales Order series `HTY-SO-.YYYY.-` is not one of the Delivery Note's
options — a regression there would be a hard save error, not a silent one.
"""

import json

import frappe
from frappe import _

HTY = "HTY"
VFY = "VFY"

# Sales Order Item fieldname -> Delivery Note Item fieldname, for the pairs
# that do NOT share a name and so are invisible to frappe's mapper.
ITEM_FIELD_MAP = {
	"custom_container_number": "custom_container_no",
	"custom_lot_number": "custom_lot_no",
	"custom_batch_no": "batch_no",
}


@frappe.whitelist()
def make_delivery_note(source_name, target_doc=None, kwargs=None):
	"""Wraps ERPNext's Sales Order -> Delivery Note mapping.

	Wired in hooks.py under `override_whitelisted_methods`, so both entry
	points route here. ERPNext does the whole mapping; we only post-process,
	and only for HTY.

	The signature MUST stay identical to the function being overridden.
	frappe calls it positionally, and the two entry points do not agree on how
	many arguments that is (frappe/model/mapper.py):

	    make_mapped_doc()   Sales Order > Create > Delivery Note
	                        -> method(source_name)                  1 arg

	    map_docs()          Delivery Note > Get Items From > Sales Order
	                        -> _args = (src, target_doc, json.loads(args))
	                              if args else (src, target_doc)
	                           method(*_args)                       3 args

	The Get Items From dialog always sends args (customer,
	allow_child_item_selection, filtered_children), so that path always calls
	with three positionals. A `**kwargs` tail accepts none of them by
	position, which is MI1-I108:

	    TypeError: make_delivery_note() takes from 1 to 2 positional
	    arguments but 3 were given

	Forwarded positionally for the same reason — ERPNext's own parameter is
	named `kwargs`, and it is a plain third argument there, not a tail.
	"""
	from erpnext.selling.doctype.sales_order.sales_order import (
		make_delivery_note as erpnext_make_delivery_note,
	)

	# Before the mapper runs: it overwrites transaction_type from the source,
	# so the Delivery Note's own mode is only readable now.
	_reject_mode_mismatch(source_name, _target_mode(target_doc))

	target = erpnext_make_delivery_note(source_name, target_doc, kwargs)
	return carry_sales_order_details(source_name, target)


def _target_mode(target_doc):
	"""The Delivery Note's own Transaction Type, read before the mapper.

	map_docs hands the open form's document over as JSON; Create > Delivery
	Note passes nothing at all, and then there is no existing note to protect.
	Anything unreadable returns None for the same reason — this guard must
	never become the reason Get Items stops working.
	"""
	if not target_doc:
		return None

	if isinstance(target_doc, str):
		try:
			target_doc = json.loads(target_doc)
		except (TypeError, ValueError):
			return None

	if isinstance(target_doc, dict):
		mode = target_doc.get("transaction_type")
	else:
		mode = getattr(target_doc, "transaction_type", None)

	return (mode or "").strip().upper() or None


def _reject_mode_mismatch(source_name, target_mode):
	"""MI1-I117: refuse to map a Sales Order onto a Delivery Note of the other
	mode.

	frappe's mapper copies transaction_type by fieldname and neither side sets
	`no_copy`, so the Sales Order's mode was written straight over the Delivery
	Note's — an HTY note silently became VFY the moment a VFY order was picked.
	Yarn is either HTY or VFY, and every VFY/HTY report keys on the mode, so a
	cross-mode note is wrong data rather than a shortcut. Name both documents
	so the user can see which one to change.
	"""
	if not target_mode:
		return

	source_mode = (
		frappe.db.get_value("Sales Order", source_name, "transaction_type") or VFY
	)
	source_mode = source_mode.strip().upper()

	if source_mode == target_mode:
		return

	frappe.throw(
		_("This Delivery Note is <b>{0}</b>, but Sales Order <b>{1}</b> is <b>{2}</b>.").format(
			target_mode, source_name, source_mode
		),
		title=_("Transaction Type Mismatch"),
	)


def carry_sales_order_details(source_name, target):
	"""Fill the fields ERPNext's mapping cannot reach. Runs in both modes.

	MI1-I117: the item work used to be gated on HTY, so a VFY Delivery Note
	came off a Sales Order with no container, lot or batch on its rows. That
	is not only missing data — every VFY/HTY report keys on those columns, and
	the `dn` report resolves the mode itself with

	    EXISTS (SELECT 1 FROM `tabContainer` c
	            WHERE c.container_no = dni.custom_container_no ...)

	which a blank container_no can never satisfy, so the whole note dropped
	out of the report. The mismatched fieldnames are a property of the two
	DocTypes, not of the mode, and the Sales Order Booking flow fills them on
	VFY rows exactly as the HTY flow does — so the copy belongs on both paths.

	Only the header lines below stay HTY-specific.
	"""
	if not target or not target.get("items"):
		return target

	source = frappe.get_doc("Sales Order", source_name)

	# MI1-I120 revision (Raj 2026-09-05): every VFY Delivery Note carries its
	# Sales Order number; the mapper has no header field to copy it from.
	if (source.get("transaction_type") or "VFY").upper() != HTY and not target.get("custom_sales_order"):
		target.custom_sales_order = source_name

	if (source.get("transaction_type") or "VFY").upper() == HTY:
		# The mapper copies the header spec by fieldname; make the mode
		# explicit so a Sales Order saved before transaction_type existed still
		# produces an HTY Delivery Note rather than a silently-VFY one.
		target.transaction_type = HTY

		# Fetch CONTROL, not data — see the module docstring.
		target.fetch_batches = 0

	source_rows = {row.name: row for row in source.items}

	# .get("items"): a frappe._dict target (tests, callers building the note
	# by hand) resolves `.items` to the dict method.
	for row in target.get("items") or []:
		# ERPNext's mapping stamps the originating Sales Order Item on
		# `so_detail`; without it there is no row to copy from.
		src = source_rows.get(row.get("so_detail"))
		if not src:
			continue

		for src_field, target_field in ITEM_FIELD_MAP.items():
			value = src.get(src_field)
			# Never overwrite something the mapping already resolved — on a
			# partial delivery ERPNext may have picked the batch itself.
			if value and not row.get(target_field):
				row.set(target_field, value)

		_protect_ordered_qty(row)

	_fill_supplier_batch_numbers(target)

	return target


def _fill_supplier_batch_numbers(target):
	"""Resolve custom_supplier_batch_no from each row's Batch.

	Delivery Note Item declares no `fetch_from` for it — custom_gross_weight
	is the only one that does — and the Sales Order Booking flow never sets it
	on the Sales Order row, so rows arrive blank however they were mapped. The
	`dn` report GROUP_CONCATs that column, so blank means an empty Supplier
	Batch No against the whole Delivery Challan.

	One query for every batch on the note, not one per row: this runs inside
	the Get Items request the user is already waiting on.
	"""
	wanted = {
		row.get("batch_no")
		for row in target.get("items") or []
		if row.get("batch_no") and not row.get("custom_supplier_batch_no")
	}
	if not wanted:
		return

	supplier_batch_by_name = dict(
		frappe.get_all(
			"Batch",
			filters={"name": ("in", list(wanted))},
			fields=["name", "custom_supplier_batch_no"],
			as_list=True,
		)
	)

	for row in target.get("items") or []:
		if row.get("custom_supplier_batch_no"):
			continue
		value = supplier_batch_by_name.get(row.get("batch_no"))
		if value:
			row.set("custom_supplier_batch_no", value)


def _protect_ordered_qty(row):
	"""Stop 'Cone Qty Calcuation' replacing the ordered qty with the batch's.

	Only for rows that would actually trip that script — it acts on rows
	carrying BOTH a batch and a cone. Seeding `custom_cone_copy` alongside
	keeps the row's own arithmetic honest if the user later clears the flag
	by editing the cone.
	"""
	if not (row.get("batch_no") and row.get("custom_cone")):
		return

	if not row.get("custom_cone_copy"):
		row.custom_cone_copy = row.get("custom_cone")

	row.custom_qty_manual_edit = 1
