"""MI1-I107 — HTY Batches show plain spec values, not 'Colour-Black'.

An HTY Container captures its specs under colour / product / type. Both
create_batches() paths fold those into the canonical custom_lusture /
custom_glue / custom_pulp columns, keeping the raw Item Specification docname,
so the Batch form showed 'Colour-Black' under a heading of "Lusture".

MI1-I107 gives HTY its own three fields carrying the plain value, and strips
the prefix from custom_grade.

The hard constraint these tests exist for: custom_lusture / custom_glue /
custom_pulp must KEEP the raw docname. mhr.utilis.get_delivery_note_batch and
mhr.note.fetch_batches both filter Batch on the exact string the Delivery Note
header holds, so rewriting those columns breaks Fetch Batches. VFY Batches —
445k of them — must not change at all.
"""

import inspect

import frappe
from frappe.tests.utils import FrappeTestCase

from mhr.utilis import (
	apply_hty_spec_values,
	grade_filter_value,
	hty_aware_specs,
	resolve_spec_value,
	strip_prefix,
)

HTY_FIELDS = ("custom_colour", "custom_product", "custom_type")
RAW_FIELDS = ("custom_lusture", "custom_glue", "custom_pulp")


class TestResolveSpecValue(FrappeTestCase):
	"""Reads Item Specification.value rather than splitting the docname.

	strip_prefix() splits on the LAST hyphen, which is wrong for records that
	exist on this site. Those values are now written into the Batch
	permanently, so a lossy split would be baked into the data."""

	def test_blank_input(self):
		for value in ("", None):
			with self.subTest(value=value):
				self.assertEqual(resolve_spec_value(value), "")

	def test_resolves_from_the_specification_record(self):
		spec = frappe.db.get_value(
			"Item Specification", {"value": ("!=", "")}, ["name", "value"], as_dict=True
		)
		if not spec:
			self.skipTest("No Item Specification with a value on this site.")
		self.assertEqual(resolve_spec_value(spec.name), spec.value)

	def test_beats_strip_prefix_on_a_hyphenated_value(self):
		"""'Grade-Off-Grade' -> 'Off-Grade'. strip_prefix gives 'Grade'."""
		name = "Grade-Off-Grade"
		if not frappe.db.exists("Item Specification", name):
			self.skipTest(f"{name} not present on this site.")
		self.assertEqual(resolve_spec_value(name), "Off-Grade")
		self.assertEqual(strip_prefix(name), "Grade")

	def test_handles_the_colon_separated_records(self):
		"""'Grade: .' has no hyphen at all, so strip_prefix strips nothing."""
		name = "Grade: ."
		if not frappe.db.exists("Item Specification", name):
			self.skipTest(f"{name} not present on this site.")
		self.assertEqual(resolve_spec_value(name), ".")
		self.assertEqual(strip_prefix(name), name)

	def test_falls_back_when_the_record_is_gone(self):
		"""A free-typed value, or a spec deleted after the Batch was made."""
		name = "Colour-NoSuchSpecificationMI1I107"
		self.assertFalse(frappe.db.exists("Item Specification", name))
		self.assertEqual(resolve_spec_value(name), strip_prefix(name))

	def test_is_idempotent_on_an_already_plain_value(self):
		"""The backfill must be safe to re-run."""
		for value in ("Black", "AA", "A EVEN"):
			with self.subTest(value=value):
				self.assertEqual(resolve_spec_value(value), resolve_spec_value(resolve_spec_value(value)))


class TestApplyHTYSpecValues(FrappeTestCase):
	def _container(self, **kwargs):
		base = {
			"transaction_type": "HTY",
			"colour": "Colour-Black",
			"product": "Product-Chips",
			"type": "Type-Bag",
			"grade": "Grade-AA",
			"glue": None,
			"lusture": None,
			"pulp": None,
		}
		base.update(kwargs)
		return frappe._dict(base)

	def test_vfy_container_is_a_no_op(self):
		"""The 445k VFY Batches must be untouched, grade included."""
		batch = frappe._dict(custom_grade="Grade-A EVEN")
		apply_hty_spec_values(batch, self._container(transaction_type="VFY"))

		self.assertEqual(batch.custom_grade, "Grade-A EVEN")
		for fieldname in HTY_FIELDS:
			with self.subTest(fieldname=fieldname):
				self.assertIsNone(batch.get(fieldname))

	def test_missing_transaction_type_is_treated_as_vfy(self):
		batch = frappe._dict()
		apply_hty_spec_values(batch, self._container(transaction_type=None))
		self.assertIsNone(batch.get("custom_colour"))

	def test_hty_container_fills_the_three_fields(self):
		batch = frappe._dict()
		apply_hty_spec_values(batch, self._container())

		self.assertEqual(batch.custom_colour, resolve_spec_value("Colour-Black"))
		self.assertEqual(batch.custom_product, resolve_spec_value("Product-Chips"))
		self.assertEqual(batch.custom_type, resolve_spec_value("Type-Bag"))

	def test_hty_grade_loses_its_prefix(self):
		batch = frappe._dict()
		apply_hty_spec_values(batch, self._container())

		self.assertEqual(batch.custom_grade, resolve_spec_value("Grade-AA"))
		self.assertNotIn("Grade-", batch.custom_grade or "")

	def test_empty_container_specs_give_empty_strings(self):
		"""Plenty of HTY Containers have no colour at all."""
		batch = frappe._dict()
		apply_hty_spec_values(batch, self._container(colour=None, grade=""))

		self.assertEqual(batch.custom_colour, "")
		self.assertEqual(batch.custom_grade, "")

	def test_never_writes_the_raw_columns(self):
		"""Those feed the Fetch Batches filters and must keep the docname."""
		source = inspect.getsource(apply_hty_spec_values)
		for fieldname in RAW_FIELDS:
			with self.subTest(fieldname=fieldname):
				self.assertNotIn(f"batch_doc.{fieldname}", source)


class TestHTYAwareSpecs(FrappeTestCase):
	"""The fold both create_batches() paths did inline, lifted out so
	heal_orphan_batch_masters uses it too."""

	def test_hty_reads_product_colour_type(self):
		specs = hty_aware_specs(
			frappe._dict(
				transaction_type="HTY", product="P", colour="C", type="T",
				glue="ignored", lusture="ignored", pulp="ignored",
			)
		)
		self.assertEqual(specs, {"glue": "P", "lusture": "C", "pulp": "T"})

	def test_vfy_reads_glue_lusture_pulp(self):
		specs = hty_aware_specs(
			frappe._dict(
				transaction_type="VFY", glue="G", lusture="L", pulp="Pu",
				product="ignored", colour="ignored", type="ignored",
			)
		)
		self.assertEqual(specs, {"glue": "G", "lusture": "L", "pulp": "Pu"})

	def test_blank_transaction_type_is_vfy(self):
		specs = hty_aware_specs(frappe._dict(glue="G", lusture="L", pulp="Pu"))
		self.assertEqual(specs["glue"], "G")


class TestGradeFilterTolerance(FrappeTestCase):
	"""HTY Batches now hold 'AA', VFY still hold 'Grade-A EVEN', and a header
	saved before the backfill can carry either."""

	def test_blank_returns_none(self):
		self.assertIsNone(grade_filter_value(""))
		self.assertIsNone(grade_filter_value(None))

	def test_prefixed_input_matches_both_forms(self):
		if not frappe.db.exists("Item Specification", "Grade-AA"):
			self.skipTest("Grade-AA not present on this site.")
		self.assertEqual(grade_filter_value("Grade-AA"), ["in", ["Grade-AA", "AA"]])

	def test_plain_input_collapses_to_a_single_value(self):
		"""'AA' resolves to itself, so there is nothing to widen."""
		self.assertEqual(grade_filter_value("AA"), "AA")

	def test_both_filter_sites_use_it(self):
		"""Named explicitly rather than searched for: a lookup that failed to
		find the function would leave this test asserting on an empty string
		and passing for the wrong reason."""
		from mhr import note, utilis

		for fn in (note.fetch_batches, utilis.get_delivery_note_batch):
			with self.subTest(function=fn.__name__):
				self.assertIn("grade_filter_value(grade)", inspect.getsource(fn))


class TestFieldsShipAsFixtures(FrappeTestCase):
	def _fixture(self):
		import json

		path = frappe.get_app_path("mhr", "fixtures", "custom_field.json")
		with open(path, encoding="utf-8") as f:
			return {r["name"]: r for r in json.load(f)}

	def test_the_three_fields_exist(self):
		records = self._fixture()
		for fieldname in HTY_FIELDS:
			with self.subTest(fieldname=fieldname):
				self.assertIn(f"Batch-{fieldname}", records)

	def test_they_are_hty_only(self):
		records = self._fixture()
		for fieldname in HTY_FIELDS:
			with self.subTest(fieldname=fieldname):
				self.assertEqual(
					records[f"Batch-{fieldname}"]["depends_on"],
					"eval:doc.custom_transaction_type=='HTY'",
				)

	def test_the_raw_trio_is_hidden_in_hty(self):
		"""Hidden, not removed — the values still drive the Fetch Batches
		filters."""
		records = self._fixture()
		for fieldname in RAW_FIELDS:
			with self.subTest(fieldname=fieldname):
				self.assertEqual(
					records[f"Batch-{fieldname}"]["depends_on"],
					"eval:doc.custom_transaction_type!='HTY'",
				)

	def test_the_three_carry_no_description(self):
		"""The form shows a field's description as grey help text under every
		row; three copies of the same implementation note is noise for the
		people entering stock."""
		records = self._fixture()
		for fieldname in HTY_FIELDS:
			with self.subTest(fieldname=fieldname):
				self.assertFalse(records[f"Batch-{fieldname}"]["description"])

	def test_grade_has_no_visibility_condition(self):
		"""MI1-I107 is explicit: no new grade field, no condition on the
		existing one. Only its value changes, and only for HTY."""
		records = self._fixture()
		self.assertFalse(records["Batch-custom_grade"]["depends_on"])
		for suffix in ("custom_hty_grade", "custom_grade_value", "custom_grade_display"):
			with self.subTest(fieldname=suffix):
				self.assertNotIn(f"Batch-{suffix}", records)


class TestBackfillPatch(FrappeTestCase):
	def test_registered_with_a_version_suffix(self):
		path = frappe.get_app_path("mhr", "patches.txt")
		with open(path, encoding="utf-8") as f:
			line = next(
				(l.strip() for l in f if "backfill_hty_batch_spec_values" in l), None
			)
		self.assertIsNotNone(line, "Patch missing from patches.txt.")
		self.assertIn("#", line, "Needs a version suffix so edits re-run.")

	def test_only_touches_hty_rows(self):
		from mhr.patches.v1_0 import backfill_hty_batch_spec_values as patch

		source = inspect.getsource(patch.execute)
		self.assertIn("'HTY'", source)

	def test_never_writes_the_raw_columns(self):
		from mhr.patches.v1_0 import backfill_hty_batch_spec_values as patch

		self.assertEqual(
			set(patch.SOURCE),
			{"custom_colour", "custom_product", "custom_type", "custom_grade"},
			"The patch must not write custom_lusture / custom_glue / custom_pulp "
			"— Fetch Batches filters on those exact strings.",
		)

	def test_grade_resolves_in_place(self):
		from mhr.patches.v1_0 import backfill_hty_batch_spec_values as patch

		self.assertEqual(patch.SOURCE["custom_grade"], "custom_grade")

	def test_commits_in_slices(self):
		"""~15k rows in one transaction holds locks long enough to block the
		Desk."""
		from mhr.patches.v1_0 import backfill_hty_batch_spec_values as patch

		self.assertGreater(patch.BATCH_SIZE, 0)
		self.assertIn("frappe.db.commit()", inspect.getsource(patch.execute))


class TestHealPatchIsNowHTYAware(FrappeTestCase):
	"""It rebuilt missing Batch masters by reading the Container's VFY fields
	directly, so an HTY Container healed into blank specs."""

	def test_uses_the_shared_fold(self):
		from mhr.patches.v1_0 import heal_orphan_batch_masters as patch

		source = inspect.getsource(patch)
		self.assertIn("hty_aware_specs(c)", source)
		self.assertIn("apply_hty_spec_values(b, c)", source)

	def test_selects_the_hty_columns(self):
		from mhr.patches.v1_0 import heal_orphan_batch_masters as patch

		source = inspect.getsource(patch)
		for column in ("transaction_type", "colour", "product", "type"):
			with self.subTest(column=column):
				self.assertIn(f'"{column}"', source)

	def test_no_longer_reads_the_vfy_fields_directly(self):
		from mhr.patches.v1_0 import heal_orphan_batch_masters as patch

		source = inspect.getsource(patch)
		for line in ("b.custom_glue = c.glue", "b.custom_lusture = c.lusture", "b.custom_pulp = c.pulp"):
			with self.subTest(line=line):
				self.assertNotIn(line, source)


class TestVFYBatchesUnchanged(FrappeTestCase):
	"""The whole point of not rewriting the raw columns."""

	def test_a_live_vfy_batch_still_has_its_prefixes(self):
		batch = frappe.db.get_value(
			"Batch",
			{"custom_transaction_type": ("!=", "HTY"), "custom_grade": ("like", "Grade-%")},
			["name", "custom_grade", "custom_lusture", "custom_glue", "custom_pulp"],
			as_dict=True,
		)
		if not batch:
			self.skipTest("No prefixed VFY Batch on this site.")

		self.assertTrue(batch.custom_grade.startswith("Grade-"))
		for fieldname in RAW_FIELDS:
			value = batch.get(fieldname)
			if value:
				with self.subTest(fieldname=fieldname):
					self.assertIn("-", value, f"{fieldname} lost its docname form.")

	def test_a_live_hty_batch_keeps_its_raw_columns(self):
		batch = frappe.db.get_value(
			"Batch",
			{"custom_transaction_type": "HTY", "custom_glue": ("like", "Product-%")},
			["name", "custom_glue", "custom_pulp"],
			as_dict=True,
		)
		if not batch:
			self.skipTest("No HTY Batch with a Product- spec on this site.")
		self.assertTrue(batch.custom_glue.startswith("Product-"))


class TestBackfillReportsItsCounts(FrappeTestCase):
	"""The counts have to be readable after a deploy. On Frappe Cloud there is
	no shell to tail the bench log from, so they also go to Error Log."""

	def test_report_writes_an_error_log_entry(self):
		from mhr.patches.v1_0 import backfill_hty_batch_spec_values as patch

		before = frappe.db.count("Error Log", {"method": patch.LOG_TITLE})
		patch._report(scanned=7, updated=3, skipped=4)
		frappe.db.commit()

		self.assertEqual(
			frappe.db.count("Error Log", {"method": patch.LOG_TITLE}), before + 1
		)

	def test_the_entry_carries_the_counts(self):
		from mhr.patches.v1_0 import backfill_hty_batch_spec_values as patch

		patch._report(scanned=11, updated=5, skipped=6)
		frappe.db.commit()

		entry = frappe.get_all(
			"Error Log",
			filters={"method": patch.LOG_TITLE},
			fields=["error"],
			order_by="creation desc",
			limit=1,
		)
		self.assertTrue(entry)
		body = entry[0]["error"]
		self.assertIn("Scanned : 11", body)
		self.assertIn("Updated : 5", body)
		self.assertIn("Skipped : 6", body)

	def test_title_is_single_line(self):
		"""frappe.log_error swaps title and message when the title contains a
		newline — a multi-line title would file the entry under the wrong
		heading and make it unfindable."""
		from mhr.patches.v1_0 import backfill_hty_batch_spec_values as patch

		self.assertNotIn("\n", patch.LOG_TITLE)

	def test_reports_even_when_there_is_nothing_to_do(self):
		"""A zero-row run must still leave a record, otherwise a deploy where
		the patch found nothing is indistinguishable from one where it never
		ran."""
		from mhr.patches.v1_0 import backfill_hty_batch_spec_values as patch

		source = inspect.getsource(patch.execute)
		self.assertIn("_report(scanned=0, updated=0, skipped=0)", source)


class TestPatchCreatesItsOwnColumns(FrappeTestCase):
	"""frappe/migrate.py runs post_model_sync patches inside
	run_schema_updates() and calls sync_fixtures() only afterwards, in
	post_schema_updates(). On the migrate that first ships MI1-I107 the three
	columns therefore do not exist when this patch runs."""

	def test_execute_ensures_columns_first(self):
		from mhr.patches.v1_0 import backfill_hty_batch_spec_values as patch

		source = inspect.getsource(patch.execute)
		self.assertIn("_ensure_columns()", source)
		self.assertLess(
			source.index("_ensure_columns()"),
			source.index("FROM `tabBatch`"),
			"The columns must be created before anything selects them.",
		)

	def test_definitions_come_from_the_fixture(self):
		"""Duplicating them in the patch would let the two drift."""
		from mhr.patches.v1_0 import backfill_hty_batch_spec_values as patch

		source = inspect.getsource(patch._ensure_columns)
		self.assertIn("custom_field.json", source)

	def test_every_field_it_needs_is_in_the_fixture(self):
		"""_ensure_columns throws if a field is missing from the fixture; this
		catches that at test time instead of mid-migrate."""
		import json

		from mhr.patches.v1_0 import backfill_hty_batch_spec_values as patch

		path = frappe.get_app_path("mhr", "fixtures", "custom_field.json")
		with open(path, encoding="utf-8") as f:
			available = {
				r["fieldname"] for r in json.load(f) if r.get("dt") == "Batch"
			}

		for fieldname in patch.SOURCE:
			if fieldname == "custom_grade":
				continue  # pre-existing, resolved in place
			with self.subTest(fieldname=fieldname):
				self.assertIn(fieldname, available)

	def test_columns_exist_after_migrate(self):
		"""End state, whichever pass created them."""
		for fieldname in HTY_FIELDS:
			with self.subTest(fieldname=fieldname):
				self.assertTrue(frappe.db.has_column("Batch", fieldname))


class TestSpecValueCacheIsRequestScoped(FrappeTestCase):
	"""The cache broke migrate once already: frappe.local is a werkzeug-style
	Local whose __getattribute__ raises AttributeError for __dict__
	(frappe/utils/local.py), so frappe.local.__dict__.setdefault blew up inside
	the patch. frappe.flags is the right home — a plain _dict on frappe.local,
	scoped to the request or job."""

	CACHE_KEY = "_mhr_spec_value_cache"

	def test_cache_lives_on_frappe_flags(self):
		source = inspect.getsource(resolve_spec_value)
		self.assertIn("frappe.flags.setdefault", source)
		self.assertNotIn("frappe.local.__dict__", source)

	def test_frappe_local_dunder_dict_really_is_unavailable(self):
		"""Pins the reason, so nobody 'simplifies' it back."""
		with self.assertRaises(AttributeError):
			frappe.local.__dict__

	def test_resolving_populates_the_cache(self):
		frappe.flags.pop(self.CACHE_KEY, None)
		spec = frappe.db.get_value("Item Specification", {}, "name")
		if not spec:
			self.skipTest("No Item Specification on this site.")

		resolve_spec_value(spec)
		self.assertIn(spec, frappe.flags[self.CACHE_KEY])

	def test_a_second_call_does_not_hit_the_database(self):
		spec = frappe.db.get_value("Item Specification", {}, "name")
		if not spec:
			self.skipTest("No Item Specification on this site.")

		first = resolve_spec_value(spec)
		frappe.flags[self.CACHE_KEY][spec] = "sentinel-value"
		self.assertEqual(resolve_spec_value(spec), "sentinel-value")

		frappe.flags[self.CACHE_KEY][spec] = first

	def test_blank_input_never_touches_the_cache(self):
		frappe.flags.pop(self.CACHE_KEY, None)
		self.assertEqual(resolve_spec_value(""), "")
		self.assertNotIn(self.CACHE_KEY, frappe.flags)
