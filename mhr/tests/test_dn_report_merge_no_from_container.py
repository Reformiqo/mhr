"""The DN report's Merge No came off the Delivery Note, not the Container.

The query selected `dn.custom_merge_no`, which set_header_container_info_from_items
fills by aggregating the note's own rows. Being per-note it showed the same
value on every row of the note whatever that row's container was, and the
aggregate itself could name a merge number no Container carries.

That is the same fault the Pulp / Glue / Lusture / Grade columns were already
fixed for, and the comment above them says so:

	Batch attributes MUST be per-row from the linked Batch — NOT from the DN
	header... every row showed the same (aggregated) header value.

Merge No belongs to the container, so it is read from the Container master —
keyed on the LOT as well, because a container_no is not unique. MCJC-1111
holds H30X against lot 6032025 and TRYL against lot 01012001, so a report row
for lot 01012001 must read TRYL alone. Keying on the container by itself put
both on every row of that container.
"""

import inspect
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from mhr.mhr.report.dn import dn as dn_report

CONTAINER = "MCJC-1111"


def _query_source():
	"""The SQL string only — the docstrings around it name the old field."""
	source = inspect.getsource(dn_report.get_data)
	return source.split("frappe.db.sql(", 1)[1].split("as_dict=True", 1)[0]


class TestTheQueryNoLongerReadsTheDeliveryNoteHeader(FrappeTestCase):
	def test_custom_merge_no_is_gone_from_the_query(self):
		self.assertNotIn("custom_merge_no", _query_source())

	def test_the_column_is_still_vfy_only(self):
		"""Existing behaviour (MI1-I64 reopen): Merge No is a VFY concept and
		is dropped in HTY, exactly as the Balance Report drops it."""
		vfy = [c["fieldname"] for c in dn_report.get_columns({"transaction_type": "VFY"})]
		hty = [c["fieldname"] for c in dn_report.get_columns({"transaction_type": "HTY"})]
		self.assertIn("merge_no", vfy)
		self.assertNotIn("merge_no", hty)

	def test_hty_does_not_pay_for_a_lookup_it_will_not_render(self):
		source = inspect.getsource(dn_report.get_data)
		self.assertIn('if transaction_type != "HTY":', source)


class TestTheMergeNumberIsResolvedPerRow(FrappeTestCase):
	"""The reported case. MCJC-1111 carries two Container documents:

	    lot 6032025   -> H30X
	    lot 01012001  -> TRYL

	and the report's two rows are both on lot 01012001.
	"""

	CONTAINER_DOCS = [
		{"container_no": CONTAINER, "lot_no": "6032025", "merge_no": "H30X"},
		{"container_no": CONTAINER, "lot_no": "01012001", "merge_no": "TRYL"},
	]

	def _resolve(self, report_rows, container_docs=None):
		docs = self.CONTAINER_DOCS if container_docs is None else container_docs
		with patch.object(frappe, "get_all", return_value=docs) as get_all:
			merge_numbers = dn_report._merge_numbers_by_container_and_lot(report_rows)
		resolved = [
			merge_numbers.get(dn_report._container_lot_key(row)) or ""
			for row in report_rows
		]
		return resolved, get_all

	def test_a_row_gets_its_own_lots_merge_number_only(self):
		resolved, _ = self._resolve(
			[
				{"container": CONTAINER, "lot_no": "01012001"},
				{"container": CONTAINER, "lot_no": "01012001"},
			]
		)
		self.assertEqual(resolved, ["TRYL", "TRYL"])

	def test_the_other_lot_gets_the_other_merge_number(self):
		resolved, _ = self._resolve([{"container": CONTAINER, "lot_no": "6032025"}])
		self.assertEqual(resolved, ["H30X"])

	def test_the_two_lots_never_bleed_into_each_other(self):
		"""Keying on the container alone is what put both on every row."""
		resolved, _ = self._resolve(
			[
				{"container": CONTAINER, "lot_no": "01012001"},
				{"container": CONTAINER, "lot_no": "6032025"},
			]
		)
		self.assertEqual(resolved, ["TRYL", "H30X"])
		for value in resolved:
			self.assertNotIn(",", value)

	def test_a_lot_with_no_container_row_is_blank_not_a_mixture(self):
		resolved, _ = self._resolve([{"container": CONTAINER, "lot_no": "9999"}])
		self.assertEqual(resolved, [""])

	def test_lots_are_matched_after_trimming_on_both_sides(self):
		resolved, _ = self._resolve(
			[{"container": CONTAINER, "lot_no": " 01012001 "}],
			container_docs=[
				{"container_no": CONTAINER, "lot_no": "01012001 ", "merge_no": " TRYL "}
			],
		)
		self.assertEqual(resolved, ["TRYL"])

	def test_two_containers_on_one_lot_are_joined(self):
		"""Rare, but it is why the values are still comma-joined."""
		resolved, _ = self._resolve(
			[{"container": CONTAINER, "lot_no": "01012001"}],
			container_docs=[
				{"container_no": CONTAINER, "lot_no": "01012001", "merge_no": "TRYL"},
				{"container_no": CONTAINER, "lot_no": "01012001", "merge_no": "H30X"},
			],
		)
		self.assertEqual(resolved, ["H30X, TRYL"])

	def test_containers_without_a_merge_number_are_skipped(self):
		"""Blank and NULL both mean 'nothing to show', not an empty entry."""
		resolved, _ = self._resolve(
			[{"container": CONTAINER, "lot_no": "01012001"}],
			container_docs=[
				{"container_no": CONTAINER, "lot_no": "01012001", "merge_no": ""},
				{"container_no": CONTAINER, "lot_no": "01012001", "merge_no": None},
			],
		)
		self.assertEqual(resolved, [""])

	def test_the_lot_is_fetched_from_the_container_master(self):
		_, get_all = self._resolve([{"container": CONTAINER, "lot_no": "01012001"}])
		self.assertIn("lot_no", get_all.call_args.kwargs["fields"])

	def test_cancelled_containers_are_excluded(self):
		_, get_all = self._resolve([{"container": CONTAINER, "lot_no": "x"}])
		self.assertEqual(get_all.call_args.kwargs["filters"]["docstatus"], ("<", 2))

	def test_it_asks_only_for_the_containers_on_the_report(self):
		_, get_all = self._resolve(
			[
				{"container": "B", "lot_no": "1"},
				{"container": "A", "lot_no": "1"},
				{"container": "B", "lot_no": "2"},
				{"container": None, "lot_no": "3"},
			]
		)
		self.assertEqual(
			get_all.call_args.kwargs["filters"]["container_no"], ("in", ["A", "B"])
		)

	def test_one_query_for_the_whole_report(self):
		"""Several Container documents share a container_no, so a join would
		multiply the report's rows — the same reason the transaction_type filter
		uses EXISTS. A per-row subquery would be the other trap."""
		_, get_all = self._resolve(
			[{"container": CONTAINER, "lot_no": "01012001"}] * 50
		)
		self.assertEqual(get_all.call_count, 1)

	def test_no_containers_means_no_query_at_all(self):
		with patch.object(frappe, "get_all") as get_all:
			self.assertEqual(
				dn_report._merge_numbers_by_container_and_lot(
					[{"container": None, "lot_no": "1"}, {"container": "", "lot_no": "2"}]
				),
				{},
			)
		get_all.assert_not_called()


class TestSupplierBatchNoIsUnchanged(FrappeTestCase):
	"""Asked about alongside Merge No, and deliberately left alone: it is not
	the same shape of bug.

	GROUP_CONCAT(DISTINCT dni.custom_supplier_batch_no) aggregates the Delivery
	Note ITEM rows inside this report row's own group, so the several values it
	shows genuinely belong to that row. Merge No was read from the note's
	header instead, which is a value from outside the row's scope.
	"""

	def test_it_still_aggregates_the_rows_own_delivery_note_items(self):
		self.assertIn(
			"GROUP_CONCAT(DISTINCT dni.custom_supplier_batch_no SEPARATOR ', ')",
			_query_source(),
		)

	def test_it_is_not_read_from_the_delivery_note_header(self):
		self.assertNotIn("dn.custom_supplier_batch_no", _query_source())
