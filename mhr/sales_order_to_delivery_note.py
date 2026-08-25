"""MI1-I90 — Sales Order -> Delivery Note for HTY.

The HTY order flow is: raise a Sales Order, then turn it into a Delivery
Note (the "Delivery Challan" of the shop floor — there is no Delivery
Challan DocType; DELIVERY CHALLAN is a report over Delivery Notes).

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

The header needs no such table: the MI1-I90 patch creates the Sales Order
HTY fields under the Delivery Note's own fieldnames, and no Delivery Note
custom field carries `no_copy`, so the mapper copies the whole spec block
by itself.


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

import frappe

HTY = "HTY"

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

	target = erpnext_make_delivery_note(source_name, target_doc, kwargs)
	return carry_hty_details(source_name, target)


def carry_hty_details(source_name, target):
	"""Fill the HTY fields ERPNext's mapping cannot reach. No-op for VFY."""
	if not target or not target.get("items"):
		return target

	source = frappe.get_doc("Sales Order", source_name)
	if (source.get("transaction_type") or "VFY").upper() != HTY:
		return target

	# The mapper copies the header spec by fieldname; make the mode explicit
	# so a Sales Order saved before transaction_type existed still produces an
	# HTY Delivery Note rather than a silently-VFY one.
	target.transaction_type = HTY

	# Fetch CONTROL, not data — see the module docstring.
	target.fetch_batches = 0

	source_rows = {row.name: row for row in source.items}

	for row in target.items:
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

	return target


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
