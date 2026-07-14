# MI1 (2026-07-14): re-heal patch for Batch.custom_transaction_type.
#
# The original patch (backfill_batch_transaction_type) ran on
# 2026-06-24, but 354,554 out of 378,409 Batches were still NULL on
# 2026-07-14 — every new batch created in the ~20 days since. The
# Batch.validate hook is supposed to auto-fill the field from the
# linked Container, but there are code paths (bulk imports,
# Container.create_batches before the Container is fully saved) that
# leave batches without a resolved transaction_type.
#
# This patch delegates to the existing backfill logic so we don't
# fork the SQL. If the same drift recurs, add another dated re-heal
# patch pointing here.

from mhr.patches.v1_0.backfill_batch_transaction_type import execute as backfill


def execute():
    backfill()
