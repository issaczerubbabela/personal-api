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
    "LEDGAR_SCRIPT_URL": ("sources", "expenses", "script_url"),
    "MONKEYTYPE_APE_KEY": ("sources", "monkeytype", "ape_key"),
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
    # Relative paths are relative to the config file, not to wherever the process was
    # started (an MCP client launches the server from its own directory).
    db_path = Path(config.setdefault("database", {}).get("path", "data/personal.db")).expanduser()
    config["database"]["path"] = str(db_path if db_path.is_absolute() else path.parent / db_path)
    return config
