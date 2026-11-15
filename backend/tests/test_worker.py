from roracle.worker import HANDLERS, build_handlers


def test_build_handlers_registers_ingest(settings, session_factory):
    h = build_handlers(settings, session_factory)
    assert set(h) == {"ingest", "citations", "acl_sync"} and callable(h["ingest"])
    assert isinstance(HANDLERS, dict)


def test_main_warns_when_scanning_disabled(settings, monkeypatch, caplog):
    from roracle import worker

    monkeypatch.setattr(worker, "get_settings", lambda: settings.model_copy(update={"clamav_host": ""}))
    monkeypatch.setattr(worker, "make_session_factory", lambda url: None)
    monkeypatch.setattr(worker, "build_handlers", lambda s, sf: {})
    monkeypatch.setattr(worker, "run_forever", lambda sf, h: None)
    with caplog.at_level("WARNING"):
        worker.main()
    assert "malware scanning disabled" in caplog.text
