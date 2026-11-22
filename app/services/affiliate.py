"""Commission hook invoked when an order is fulfilled.

Task 8 REPLACES this module in place with the real commission engine. Keep the
signature stable: ``apply_order_commission(db, *, order) -> list``. Real callers
must derive deterministic idempotency keys from the logical operation (the
order's ``no`` / webhook event id), never random defaults.
"""


def apply_order_commission(db, *, order) -> list:
    """No-op stub — Task 8 replaces this with commission calculations."""
    return []
