"""MI1-I107 — plain spec values on existing HTY Batches.

An HTY Container captures its specs under colour / product / type, and
create_batches() folds them into the canonical columns, keeping the raw Item
Specification docname:

    Container.colour  'Colour-Black'  -> Batch.custom_lusture
    Container.product 'Product-Chips' -> Batch.custom_glue
    Container.type    'Type-Bag'      -> Batch.custom_pulp
    Container.grade   'Grade-AA'      -> Batch.custom_grade

The Batch form therefore showed 'Colour-Black' under a heading of "Lusture".
MI1-I107 gives HTY its own three fields holding the plain value, and strips the
prefix from custom_grade.

This backfills the ~15k HTY Batches that already exist. New ones are handled by
mhr.utilis.apply_hty_spec_values on the create paths.

What it does NOT touch
----------------------
custom_lusture / custom_glue / custom_pulp keep the raw docname. Live code
matches Batch on those exact strings — mhr.utilis.get_delivery_note_batch and
mhr.note.fetch_batches both filter by the value the Delivery Note header holds
— so rewriting them would break Fetch Batches. The plain values live in the new
columns instead.

VFY Batches (445k of them) are not touched at all, including their grade.

Resolving the value
-------------------
Through mhr.utilis.resolve_spec_value, which reads Item Specification.value
rather than splitting the docname on its last hyphen. Splitting is wrong for
records that exist on this site:

    'Grade-Off-Grade'       -> 'Grade'      (should be 'Off-Grade')
    'Grade: .'              -> 'Grade: .'   (colon; nothing stripped)
    'Lusture-Special Silky' -> 'Special Silky', but that record's value is
                               ' SPECIAL SILKY'

Idempotent: re-running resolves the same values and writes the same rows.
Batches whose four columns already match are skipped, so a second run is a
no-op rather than 15k pointless writes.
"""

import frappe

BATCH_SIZE = 500

# Single line on purpose: frappe.log_error swaps title and message when the
# title contains a newline, which would file the entry under the wrong heading.
LOG_TITLE = "MI1-I107 HTY Batch spec value backfill"

# Batch column -> the column holding the raw Item Specification docname.
# custom_grade resolves in place: MI1-I107 asks for no new grade field and no
# visibility condition on it, just a value without the 'Grade-' prefix.
SOURCE = {
	"custom_colour": "custom_lusture",
	"custom_product": "custom_glue",
	"custom_type": "custom_pulp",
	"custom_grade": "custom_grade",
}


def _ensure_columns():
	"""Create the three new columns if this patch got here first.

	frappe/migrate.py runs post_model_sync patches inside run_schema_updates()
	and only calls sync_fixtures() afterwards, in post_schema_updates(). So on
	the migrate that first ships MI1-I107, custom_colour / custom_product /
	custom_type do not exist yet and the SELECT below dies with
	"Unknown column".

	The definitions are read from the same fixture file that will create them a
	few seconds later, so there is one source of truth and no chance of the two
	drifting. create_custom_fields upserts, so the fixture pass that follows is
	a no-op.
	"""
	import json

	from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

	wanted = {field for field in SOURCE if field != "custom_grade"}
	missing = {field for field in wanted if not frappe.db.has_column("Batch", field)}
	if not missing:
		return

	path = frappe.get_app_path("mhr", "fixtures", "custom_field.json")
	with open(path, encoding="utf-8") as f:
		records = json.load(f)

	definitions = [
		{
			key: value
			for key, value in record.items()
			if key not in ("doctype", "name", "modified", "docstatus", "dt")
		}
		for record in records
		if record.get("dt") == "Batch" and record.get("fieldname") in missing
	]
	if not definitions:
		frappe.throw(
			f"MI1-I107: {sorted(missing)} missing from Batch and absent from "
			"mhr/fixtures/custom_field.json — cannot back-fill."
		)

	create_custom_fields({"Batch": definitions}, update=True)
	frappe.clear_cache(doctype="Batch")


def execute():
	from mhr.utilis import resolve_spec_value

	_ensure_columns()

	rows = frappe.db.sql(
		"""
		SELECT name, custom_lusture, custom_glue, custom_pulp, custom_grade,
		       custom_colour, custom_product, custom_type
		FROM `tabBatch`
		WHERE IFNULL(custom_transaction_type, '') = 'HTY'
		""",
		as_dict=True,
	)
	if not rows:
		_report(scanned=0, updated=0, skipped=0)
		return

	updated = skipped = 0
	pending = 0

	for row in rows:
		values = {
			target: resolve_spec_value(row.get(source)) for target, source in SOURCE.items()
		}

		# Skip rows that already hold exactly these values, so a re-run costs
		# nothing. '' and NULL are the same thing here.
		if all((row.get(target) or "") == (value or "") for target, value in values.items()):
			skipped += 1
			continue

		frappe.db.set_value("Batch", row["name"], values, update_modified=False)
		updated += 1
		pending += 1

		# Commit in slices: one transaction over 15k rows holds locks long
		# enough to block the Desk, and a failure would roll the lot back.
		if pending >= BATCH_SIZE:
			frappe.db.commit()
			pending = 0

	if pending:
		frappe.db.commit()

	_report(scanned=len(rows), updated=updated, skipped=skipped)


def _report(scanned, updated, skipped):
	"""Leave the counts where they can be read after a deploy.

	Error Log rather than only the bench log, because on Frappe Cloud there is
	no shell to tail logs from — this shows up in the Desk under
	Error Log, searchable by the title below.

	log_error's own docstring warns that it swaps title and message when the
	title contains a newline, so the title is kept to one line and both
	arguments are passed by keyword. Passing no message at all would make it
	attach the current traceback, which would be meaningless here.
	"""
	summary = (
		f"Scanned : {scanned} HTY Batches\n"
		f"Updated : {updated}\n"
		f"Skipped : {skipped} (already held the resolved values)\n\n"
		"Fields written: custom_colour, custom_product, custom_type, custom_grade.\n"
		"custom_lusture / custom_glue / custom_pulp were deliberately NOT touched — "
		"Fetch Batches filters on those exact strings.\n"
		"VFY Batches were not touched at all."
	)

	frappe.logger().info(
		f"[MI1-I107] HTY Batch spec values: {updated} updated, "
		f"{skipped} already correct, {scanned} scanned."
	)
	frappe.log_error(title=LOG_TITLE, message=summary)
	frappe.db.commit()
