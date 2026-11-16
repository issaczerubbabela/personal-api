"""Load config.toml, with secrets overridable from environment variables."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

ENV_OVERRIDES = {
    "PAPI_TOKEN": ("api", "token"),
    "PAPI_DB": ("database", "path"),
    "GITHUB_TOKEN": ("sources", "github", "token"),
    "CALENDAR_ICS_URL": ("sources", "calendar", "ics_url"),
}


def load_config(path: str | os.PathLike | None = None) -> dict[str, Any]:
    path = Path(path or os.environ.get("PAPI_CONFIG", "config.toml"))
    config: dict[str, Any] = {}
    if path.exists():
        with path.open("rb") as f:
            config = tomllib.load(f)
    for env, keys in ENV_OVERRIDES.items():
        if value := os.environ.get(env):
            node = config
            for key in keys[:-1]:
                node = node.setdefault(key, {})
            node[keys[-1]] = value
    config.setdefault("api", {})
    config.setdefault("profile", {})
    config.setdefault("sources", {})
    config.setdefault("database", {}).setdefault("path", "data/personal.db")
    return config
