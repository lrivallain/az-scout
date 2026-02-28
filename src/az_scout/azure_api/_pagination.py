"""ARM pagination helper."""

from __future__ import annotations

import requests


def _paginate(url: str, headers: dict[str, str], timeout: int = 30) -> list[dict]:
    """Fetch all pages from an ARM list endpoint and return the merged values."""
    items: list[dict] = []
    while url:
        resp = requests.get(url, headers=headers, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        items.extend(data.get("value", []))
        url = data.get("nextLink")
    return items
