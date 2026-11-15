from roracle.config import get_settings
from roracle.db import make_session_factory
from roracle.queue import Handler, run_forever

HANDLERS: dict[str, Handler] = {}  # P2 registers "ingest"


def main() -> None:
    run_forever(make_session_factory(get_settings().database_url), HANDLERS)
