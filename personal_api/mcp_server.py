"""MCP server: lets an AI agent query the same local store the HTTP API serves.

Read-only. Runs over stdio, so it is only reachable by a client on this machine that
launches it; there is no network listener and therefore no token.
"""

from __future__ import annotations

from contextlib import closing
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from . import queries
from .config import load_config
from .db import connect

INSTRUCTIONS = """\
Personal data for one person, synced periodically from their own services into a local
database. Data is as fresh as the last sync (see get_profile). All timestamps are UTC.
Records share one shape: id, source, type, timestamp, title, content, url, data.
Sources and types (only the ones get_profile lists are set up):
- github: event, repo          - calendar: event
- expenses: expense, income, transfer
- leetcode: solved, day (submissions per active day)
- monkeytype: test             - obsidian: note
- browser: visit (only returned when you pass source="browser")
For money questions use spending_summary rather than adding up rows. For headline numbers
(problems solved, typing speed, streaks) use get_stats. Lists cut long content such as
notes to a snippet marked content_truncated; call get_record with the id for the rest."""

READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=False)


def create_server(config: dict[str, Any] | None = None) -> MCPServer:
    config = config or load_config()
    db_path = config["database"]["path"]
    server = MCPServer("personal-api", instructions=INSTRUCTIONS)

    @server.tool(annotations=READ_ONLY)
    def get_profile() -> dict[str, Any]:
        """Who this person is (name, bio, links), which sources are synced, how many
        records each has, the date range covered, and when each last synced."""
        with closing(connect(db_path)) as conn:
            return queries.profile(conn, config)

    @server.tool(annotations=READ_ONLY)
    def get_activity(since: str | None = None, until: str | None = None,
                     source: str | None = None, type: str | None = None,
                     limit: int = 50, offset: int = 0) -> dict[str, Any]:
        """Timeline of what the person did, newest first, across all sources.

        since/until are ISO dates or datetimes (since inclusive, until exclusive), e.g.
        since="2026-03-01", until="2026-04-01" for March. Filter with source and type
        (see the server instructions for the values).
        Returns {total, items}; page with limit/offset when total exceeds limit."""
        with closing(connect(db_path)) as conn:
            return queries.activity(conn, since, until, source, type,
                                    max(1, min(limit, 500)), max(0, offset))

    @server.tool(annotations=READ_ONLY)
    def list_projects(include_forks: bool = False, include_private: bool = False,
                      include_archived: bool = True) -> dict[str, Any]:
        """The person's GitHub repositories, most recently pushed first, with description,
        language, topics and stars. Private repos are excluded unless include_private."""
        with closing(connect(db_path)) as conn:
            return queries.projects(conn, include_forks, include_private, include_archived)

    @server.tool(annotations=READ_ONLY)
    def search(query: str, source: str | None = None, limit: int = 20) -> dict[str, Any]:
        """Full-text search over record titles and content (notes, repo names and
        descriptions, event titles, transactions, page titles of visited sites). Every word must
        match; a word also matches as a prefix. Use a few keywords, not a sentence."""
        with closing(connect(db_path)) as conn:
            return queries.search(conn, query, source, max(1, min(limit, 200)))

    @server.tool(annotations=READ_ONLY)
    def get_record(id: int) -> dict[str, Any]:
        """One record in full by its id, e.g. the whole text of a note whose content was
        truncated in get_activity or search results."""
        with closing(connect(db_path)) as conn:
            found = queries.record(conn, id)
        if not found:
            raise ValueError(f"no record with id {id}")
        return found

    @server.tool(annotations=READ_ONLY)
    def get_stats() -> dict[str, Any]:
        """Current headline numbers per source: LeetCode problems solved by difficulty,
        ranking and streak; Monkeytype tests, hours typing and best wpm per mode."""
        with closing(connect(db_path)) as conn:
            return queries.stats(conn)

    @server.tool(annotations=READ_ONLY)
    def spending_summary(since: str | None = None, until: str | None = None,
                         group_by: str = "category", type: str = "expense") -> dict[str, Any]:
        """Money totals for a period. group_by is "category", "month", "day" or "account";
        type is "expense" (default), "income" or "transfer". since is inclusive, until
        exclusive. Returns the overall total and count plus one row per group."""
        with closing(connect(db_path)) as conn:
            return queries.spending(conn, since, until, group_by, type)

    return server
