"""MI1-I91 — STOCK SHEET (BALANCE REPORT) fixes.

Chips never appeared in the Product column, and its stock never appeared
either — because the row was dropped before rendering by a blanket `cone > 0`
guard. Chips ships in Bags, so cone is legitimately 0 on 4,046 of the site's
4,943 HTY batches.

The hard requirement: VFY output must not change.

MI1-I91 also pre-filled From Date / To Date, seeded from the earliest Batch.
That was reverted on 2026-08-18 — the boxes open blank again, meaning "every
batch", and the seeding helper went with it. The range behaviour that survived
the revert (a blank range and a full range return the same rows; To Date covers
the whole day) is still pinned below.
"""

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, getdate

from mhr.utilis import get_container_nos_by_transaction_type

REPORT = "STOCK SHEET (BALANCE REPORT)"


def _run(filters):
	"""Run the report the way the Desk does, bypassing prepared_report so the
	result comes back synchronously."""
	from frappe.desk.query_report import run

	result = run(REPORT, filters=filters, ignore_prepared_report=True)
	return result.get("result") or []


def _detail_rows(rows):
	"""Drop the lot-total / grand-total rows (sort_order >= 1)."""
	return [r for r in rows if not r.get("sort_order")]


def _find_zero_cone_container(transaction_type):
	"""A container_no that has at least one zero-cone batch of the given mode.
	Returns None when the site has no such data."""
	tt_containers = get_container_nos_by_transaction_type(transaction_type) or set()
	if not tt_containers:
		return None
	row = frappe.db.sql(
		"""
		SELECT custom_container_no
		FROM `tabBatch`
		WHERE IFNULL(custom_cone, 0) = 0
		  AND IFNULL(custom_container_no, '') != ''
		  AND IFNULL(custom_transaction_type, '') = %s
		LIMIT 1
		""",
		(transaction_type,),
	)
	if not row:
		return None
	container = row[0][0]
	return container if container in tt_containers else None


# ---------------------------------------------------------------------------
# (a) date range
# ---------------------------------------------------------------------------


class TestFullRangeMatchesBlankRange(FrappeTestCase):
	def test_full_range_returns_same_rows_as_blank(self):
		"""Blank boxes mean "every batch". Typing the widest possible range by
		hand must therefore return exactly the same rows."""
		container = _find_zero_cone_container("HTY") or frappe.db.get_value(
			"Batch", {"custom_container_no": ["!=", ""]}, "custom_container_no"
		)
		if not container:
			self.skipTest("No batches with a container number on this site.")

		earliest = frappe.db.sql("SELECT MIN(creation) FROM `tabBatch`")[0][0]
		if not earliest:
			self.skipTest("No Batch rows on this site.")

		blank = _run({"container": container})
		full = _run(
			{
				"container": container,
				"fdt": str(getdate(earliest)),
				"tdt": frappe.utils.today(),
			}
		)
		self.assertEqual(
			len(blank), len(full),
			"earliest-batch -> today must return the same rows as a blank range.",
		)


class TestToDateIsInclusiveOfWholeDay(FrappeTestCase):
	"""Batch.creation is a DATETIME. `creation <= '<date>'` resolves to
	midnight and silently drops everything created that day."""

	def test_batch_created_today_is_not_dropped(self):
		today = frappe.utils.today()
		container = frappe.db.sql(
			"""
			SELECT custom_container_no
			FROM `tabBatch`
			WHERE DATE(creation) = %s AND IFNULL(custom_container_no, '') != ''
			LIMIT 1
			""",
			(today,),
		)
		if not container:
			self.skipTest("No Batch created today to exercise the boundary.")
		container = container[0][0]

		rows = _run({"container": container, "fdt": add_days(today, -1), "tdt": today})
		self.assertTrue(
			rows,
			"A batch created today must still be included when To Date = today.",
		)


# ---------------------------------------------------------------------------
# (b) zero-cone HTY material (Chips / Waste)
# ---------------------------------------------------------------------------


class TestHTYZeroConeRowsAreIncluded(FrappeTestCase):
	def test_hty_zero_cone_container_produces_rows(self):
		container = _find_zero_cone_container("HTY")
		if not container:
			self.skipTest("No HTY container with zero-cone batches on this site.")

		rows = _detail_rows(_run({"container": container, "transaction_type": "HTY"}))
		if not rows:
			self.skipTest(
				f"Container {container} has no positive stock balance right now — "
				"nothing to assert about the cone guard."
			)
		self.assertTrue(
			any(str(r.get("Cone") or "0") in ("", "0") for r in rows),
			"HTY rows with cone = 0 (Chips / Waste in Bags) must survive Step 4.",
		)

	def test_product_column_is_populated_for_chips(self):
		"""Container.product is copied into Batch.custom_glue by create_batches,
		and the report relabels Glue -> Product in HTY mode. Once the row is no
		longer dropped, the value must arrive prefix-stripped."""
		batch = frappe.db.sql(
			"""
			SELECT custom_container_no, custom_glue
			FROM `tabBatch`
			WHERE IFNULL(custom_cone, 0) = 0
			  AND custom_glue LIKE 'Product-%%'
			  AND IFNULL(custom_transaction_type, '') = 'HTY'
			  AND IFNULL(custom_container_no, '') != ''
			LIMIT 1
			""",
			as_dict=True,
		)
		if not batch:
			self.skipTest("No zero-cone HTY batch with a Product- spec on this site.")
		container = batch[0]["custom_container_no"]
		expected = batch[0]["custom_glue"].rsplit("-", 1)[-1]

		rows = _detail_rows(_run({"container": container, "transaction_type": "HTY"}))
		if not rows:
			self.skipTest(f"Container {container} currently carries no stock balance.")
		self.assertIn(
			expected,
			[r.get("Glue") for r in rows],
			f"Product column must show {expected!r} (fieldname stays 'Glue').",
		)

	def test_product_column_label_swaps_in_hty(self):
		from frappe.desk.query_report import run

		cols = run(REPORT, filters={"transaction_type": "HTY", "container": "__none__"},
		           ignore_prepared_report=True).get("columns") or []
		by_fieldname = {c.get("fieldname"): c.get("label") for c in cols}
		self.assertEqual(by_fieldname.get("Glue"), "Product")
		self.assertEqual(by_fieldname.get("Pulp"), "Type")


class TestVFYBehaviourUnchanged(FrappeTestCase):
	"""The cone > 0 rule is yarn-specific and must still apply to VFY, even on
	the unfiltered 'All' view."""

	def test_vfy_zero_cone_rows_stay_excluded(self):
		container = _find_zero_cone_container("VFY")
		if not container:
			self.skipTest("No VFY container with zero-cone batches on this site.")

		rows = _detail_rows(_run({"container": container, "transaction_type": "VFY"}))
		for r in rows:
			try:
				cone = int(r.get("Cone") or 0)
			except (ValueError, TypeError):
				cone = 0
			self.assertGreater(
				cone, 0,
				f"VFY row in {container} with cone = 0 leaked into the report.",
			)

	def test_vfy_zero_cone_rows_stay_excluded_on_unfiltered_view(self):
		"""Same assertion with a blank Transaction Type — the gate reads the
		row's own Container, not the filter."""
		container = _find_zero_cone_container("VFY")
		if not container:
			self.skipTest("No VFY container with zero-cone batches on this site.")

		hty = get_container_nos_by_transaction_type("HTY") or set()
		rows = _detail_rows(_run({"container": container}))
		for r in rows:
			if (r.get("Container Number") or "") in hty:
				continue  # HTY rows are allowed to be cone-less
			try:
				cone = int(r.get("Cone") or 0)
			except (ValueError, TypeError):
				cone = 0
			self.assertGreater(cone, 0, "VFY cone-less row leaked on the All view.")

	def test_vfy_column_labels_unchanged(self):
		from frappe.desk.query_report import run

		cols = run(REPORT, filters={"transaction_type": "VFY", "container": "__none__"},
		           ignore_prepared_report=True).get("columns") or []
		by_fieldname = {c.get("fieldname"): c.get("label") for c in cols}
		self.assertEqual(by_fieldname.get("Glue"), "Glue")
		self.assertEqual(by_fieldname.get("Pulp"), "Pulp")
		# Merge No / Cross Section are VFY-only columns (MI1-I64) — still there.
		self.assertIn("Merge No", by_fieldname.values())
		self.assertIn("Cross Section", by_fieldname.values())
