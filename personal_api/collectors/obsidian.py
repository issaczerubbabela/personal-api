"""Obsidian: a vault is a folder of Markdown files, so this just reads them.

type=note, one per .md file, timestamped by last modification. Deleted or renamed notes
are removed on the next sync.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from ..db import Record, to_utc
from . import CollectResult

FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n?", re.DOTALL)
INLINE_TAG = re.compile(r"(?:^|\s)#([A-Za-z][\w/-]*)")
WIKILINK = re.compile(r"\[\[([^\]|#]+)")


def _frontmatter_tags(block: str) -> list[str]:
    tags: list[str] = []
    lines = block.splitlines()
    for i, line in enumerate(lines):
        if not re.match(r"tags?\s*:", line):
            continue
        inline = line.split(":", 1)[1].strip().strip("[]")
        if inline:
            tags += [t.strip().strip("'\"#") for t in re.split(r"[,\s]+", inline) if t.strip()]
        for item in lines[i + 1:]:
            if not item.lstrip().startswith("- "):
                break
            tags.append(item.lstrip()[2:].strip().strip("'\"#"))
    return tags


def collect(settings: dict[str, Any], ctx: Any = None) -> CollectResult:
    if not settings.get("vault"):
        raise ValueError("sources.obsidian.vault is not set")
    vault = Path(settings["vault"]).expanduser()
    if not vault.is_dir():
        raise FileNotFoundError(f"vault folder not found: {vault}")
    exclude = {".obsidian", ".trash", *settings.get("exclude", [])}

    records: list[Record] = []
    for path in sorted(vault.rglob("*.md")):
        relative = path.relative_to(vault)
        parts = relative.parts
        # Excluded by top-level folder, any folder name, or full relative path prefix.
        if any(p in exclude or p.startswith(".") for p in parts[:-1]) or \
                any(relative.as_posix().startswith(e.rstrip("/") + "/") for e in exclude):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        tags: list[str] = []
        if match := FRONTMATTER.match(text):
            tags = _frontmatter_tags(match.group(1))
            text = text[match.end():]
        tags += INLINE_TAG.findall(text)
        stat = path.stat()
        records.append(Record(
            source="obsidian", type="note", external_id=relative.as_posix(),
            timestamp=to_utc(datetime.fromtimestamp(stat.st_mtime, timezone.utc)),
            title=path.stem, content=text.strip(),
            url=f"obsidian://open?vault={quote(vault.name)}&file={quote(relative.as_posix())}",
            data={
                "path": relative.as_posix(),
                "folder": relative.parent.as_posix() if len(parts) > 1 else "",
                "tags": sorted(set(t for t in tags if t)),
                "links": sorted(set(link.strip() for link in WIKILINK.findall(text))),
                "words": len(text.split()),
            },
        ))
    return CollectResult(records, replace_window=("0000", "9999") if records else None)
