from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "frontend" / "nginx" / "25-api-proxy.sh"


def test_metrics_blocked_before_api_proxy():
    text = SCRIPT.read_text()
    assert "location ^~ /api/metrics { return 404; }" in text
    assert text.index("/api/metrics") < text.index("location /api/ {")


def test_readyz_blocked_before_api_proxy():
    text = SCRIPT.read_text()
    assert "location = /api/readyz { return 404; }" in text
    assert text.index("/api/readyz") < text.index("location /api/ {")
