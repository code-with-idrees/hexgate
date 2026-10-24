"""Provider catalog: the static, versioned list of known providers.

The catalog is data, not code. Adding a provider means adding an entry to
``hexgate/data/providers.json`` — no Python changes, no release required for
users who point at their own copy.
"""

from __future__ import annotations

import json
from functools import lru_cache
from importlib.resources import files
from typing import Any


@lru_cache(maxsize=1)
def load_catalog() -> tuple[dict[str, Any], ...]:
    raw = files("hexgate").joinpath("data/providers.json").read_text(encoding="utf-8")
    data = json.loads(raw)
    if not isinstance(data, list):  # pragma: no cover - guards bad data edits
        raise ValueError("providers.json must contain a list")
    seen: set[str] = set()
    for entry in data:
        pid = entry.get("id")
        if not pid or pid in seen:
            raise ValueError(f"duplicate or missing provider id: {pid!r}")
        seen.add(pid)
    return tuple(data)


def catalog_entry(provider_id: str) -> dict[str, Any] | None:
    for entry in load_catalog():
        if entry["id"] == provider_id:
            return dict(entry)
    return None


def provider_ids() -> list[str]:
    return [entry["id"] for entry in load_catalog()]


def tier_order(tier: str) -> int:
    return {"free": 0, "trial": 1, "subscription": 2, "paid": 3, "local": 4, "custom": 5}.get(
        tier, 5
    )
