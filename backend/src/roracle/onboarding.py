"""Server-tracked "Get started" checklist. mark() is called from real event paths (hot), so once a
step is known present for a user it is a no-op in-process. Keys are held in session.info until the
transaction commits (after_commit promotes them to the cache; after_rollback drops them), so a
rolled-back transaction cannot poison the cache."""

from uuid import UUID

from sqlalchemy import event, text
from sqlalchemy.orm import Session

STEPS: tuple[str, ...] = ("search", "ask", "upload", "collect", "token", "mcp", "handoff")
_known: set[tuple[UUID, str]] = set()

_MARK = text(
    "INSERT INTO onboarding_progress (user_id, steps) VALUES (:u, ARRAY[:s]) "
    "ON CONFLICT (user_id) DO UPDATE SET steps = array_append(onboarding_progress.steps, :s), "
    "updated_at = now() WHERE NOT (:s = ANY(onboarding_progress.steps))"
)
_DISMISS = text(
    "INSERT INTO onboarding_progress (user_id, dismissed) VALUES (:u, :d) "
    "ON CONFLICT (user_id) DO UPDATE SET dismissed = :d, updated_at = now()"
)


def mark(session: Session, user_id: UUID, step: str) -> None:
    if step not in STEPS:
        raise ValueError(f"unknown onboarding step: {step}")
    key = (user_id, step)
    if key in _known:
        return
    session.execute(_MARK, {"u": user_id, "s": step})  # inserted/appended, or already present
    session.info.setdefault("onb_pending", set()).add(key)


def mark_human(session: Session, p, step: str) -> None:
    """Steps that stand for a human's web action: an agent token (PAT or /mcp) never ticks them."""
    if not p.is_agent:
        mark(session, p.user_id, step)


def state(session: Session, user_id: UUID) -> dict:
    row = session.execute(
        text("SELECT steps, dismissed FROM onboarding_progress WHERE user_id = :u"), {"u": user_id}
    ).first()
    steps, dismissed = (row[0], row[1]) if row else ([], False)
    return {"steps": [s for s in STEPS if s in steps], "dismissed": dismissed}


def set_dismissed(session: Session, user_id: UUID, dismissed: bool) -> None:
    session.execute(_DISMISS, {"u": user_id, "d": dismissed})


def reset_cache() -> None:
    _known.clear()


@event.listens_for(Session, "after_commit")
def _promote(session: Session) -> None:
    _known.update(session.info.pop("onb_pending", ()))


@event.listens_for(Session, "after_rollback")
def _drop(session: Session) -> None:
    session.info.pop("onb_pending", None)
