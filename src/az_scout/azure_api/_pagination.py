"""ARM pagination helper."""

from __future__ import annotations

import logging
from typing import Any

import requests

logger = logging.getLogger(__name__)


def _paginate(url: str, headers: dict[str, str], timeout: int = 30) -> list[dict[str, Any]]:
    """Fetch all pages from an ARM list endpoint and return the merged values."""
    items: list[dict[str, Any]] = []
    page_count = 0
    while url:
        resp = requests.get(url, headers=headers, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        page_items = data.get("value", [])
        items.extend(page_items)
        page_count += 1
        url = data.get("nextLink")
    logger.debug("ARM paginate: %d pages, %d items total", page_count, len(items))
    return items
