from roracle.worker import HANDLERS, build_handlers


def test_build_handlers_registers_ingest(settings, session_factory):
    h = build_handlers(settings, session_factory)
    assert set(h) == {"ingest"} and callable(h["ingest"])
    assert isinstance(HANDLERS, dict)
