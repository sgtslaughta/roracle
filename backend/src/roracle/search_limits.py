"""Search request limits and the error for violating them (no route/framework imports)."""

Q_MAX, LIST_MAX, VALUE_MAX = 1000, 50, 200


class BadInput(ValueError):
    """Caller-supplied search parameter outside the documented limits (REST: 422, MCP: tool error)."""
