"""EXPLAIN helpers for plan-shape tests and the `perf explain` command."""

from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session


def plan(session: Session, stmt: Any, params: dict | None = None) -> dict:
    """`EXPLAIN (FORMAT JSON, ANALYZE)` of a SQLAlchemy statement or SQL string; the top-level plan dict.
    ANALYZE runs the statement: the caller's transaction should be rolled back afterwards."""
    if isinstance(stmt, str):
        sql, params = stmt, params or {}
    else:
        c = stmt.compile(session.get_bind(), compile_kwargs={"literal_binds": True})
        sql, params = str(c).replace(":", "\\:"), {}  # a literal ':' (casts) is not a bind parameter
    return session.execute(text("EXPLAIN (FORMAT JSON, ANALYZE) " + sql), params).scalar()[0]


def nodes(p: dict):
    node = p.get("Plan", p)
    stack = [node]
    while stack:
        n = stack.pop()
        yield n
        stack.extend(n.get("Plans", []))


def seq_scans(p: dict) -> set[str]:
    """Relation names scanned sequentially anywhere in the plan."""
    return {n["Relation Name"] for n in nodes(p) if n.get("Node Type") == "Seq Scan"}


def index_names(p: dict) -> set[str]:
    return {n["Index Name"] for n in nodes(p) if "Index Name" in n}


def has_node(p: dict, node_type: str) -> bool:
    return any(n.get("Node Type") == node_type for n in nodes(p))
