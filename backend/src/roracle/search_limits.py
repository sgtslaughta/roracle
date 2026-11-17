"""Search request limits and the error for violating them (no route/framework imports)."""

Q_MAX, LIST_MAX, VALUE_MAX, FACET_MAX = 1000, 50, 200, 50
FIND_MAX = 100  # facet find box, characters
MAX_DOCS = 200  # query path candidates and the cited window
BROWSE_WINDOW = 10_000  # ES `from + size` limit and `track_total_hits`; deeper paging uses `after`
BROWSE_TRIES = 3  # ES page requests per browse page when the Postgres re-check drops rows
AFTER_MAX = 1000  # opaque `after` cursor length (ES sort values incl. a 128-char sort key)


class BadInput(ValueError):
    """Caller-supplied search parameter outside the documented limits (REST: 422, MCP: tool error)."""
