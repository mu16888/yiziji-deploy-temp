"""AIGC compliance hook (temporary passthrough; Task 11 replaces in place).

Task 11 owns the real AIGC labeling and audit implementation. Generation and
export call ``mark_aigc`` so the integration point exists from day one.
"""


def mark_aigc(result: dict) -> dict:
    """Return the result unchanged. T11 adds labels/audit here."""
    return result
