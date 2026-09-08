import subprocess
import sys
from textwrap import dedent

import pytest
from fastapi.testclient import TestClient

from backend.nospoil_nfl.api.http import create_app, parse_origins
from backend.nospoil_nfl.api.calendar import SeasonCalendar
from backend.nospoil_nfl.api.snapshots import SnapshotResult
from backend.nospoil_nfl.game.read_repository import GameRepositoryError


class FakeService:
    def __init__(self, payload=None, *, etag='"v1"', error=None):
        self.payload = payload or {"season": 2026, "games": [{"id": "1"}]}
        self.etag, self.error = etag, error
        self.reads = []
        self.calendar = SeasonCalendar()

    def _result(self):
        if self.error:
            raise self.error
        return SnapshotResult(self.payload, self.etag)

    def bootstrap(self): self.reads.append("bootstrap"); return self._result()
    def week_snapshot(self, season, phase, week):
        self.calendar.validate_week(season, phase, week)
        self.reads.append((season, phase, week)); return self._result()
    def season_snapshot(self, season):
        self.calendar.validate_season(season)
        self.reads.append((season,)); return self._result()


def client(service, origins=("https://app.example",)):
    return TestClient(create_app(service, origins=list(origins)))


def test_success_empty_conditional_head_and_changed_representation():
    service = FakeService({"season": 2026, "games": []}, etag='"empty"')
    c = client(service)
    r = c.get('/api/v1/weeks/2026/regular_season/1')
    assert r.status_code == 200 and r.json()["games"] == []
    for value in ['"empty"', 'W/"empty"', '"no", "empty"', '*']:
        r = c.get('/api/v1/bootstrap', headers={"If-None-Match": value})
        assert r.status_code == 304 and r.text == "" and r.headers["etag"] == '"empty"'
        assert r.headers["cache-control"] == "public, max-age=0, must-revalidate"
    head = c.head('/api/v1/bootstrap')
    assert head.status_code == 200 and head.text == ""
    assert head.headers["etag"] == '"empty"' and head.headers["cache-control"] == "public, max-age=0, must-revalidate"
    service.etag, service.payload = '"new"', {"season": 2026, "games": [{"id": "2"}]}
    assert c.get('/api/v1/bootstrap', headers={"If-None-Match": '"empty"'}).status_code == 200


def test_validation_unknown_without_repository_read_and_post():
    service = FakeService()
    c = client(service)
    assert c.get('/api/v1/weeks/no/regular_season/1').status_code == 422
    assert c.get('/api/v1/weeks/2026/not-a-phase/1').status_code == 422
    assert c.get('/api/v1/weeks/999/regular_season/1').status_code == 404
    assert c.get('/api/v1/weeks/2026/regular_season/99').status_code == 404
    assert service.reads == []
    assert c.post('/api/v1/bootstrap').status_code == 405


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/weeks/2026.0/regular_season/1",
        "/api/v1/weeks/%2B2026/regular_season/1",
        "/api/v1/weeks/2026/regular_season/0",
        "/api/v1/weeks/2026/regular_season/-1",
        "/api/v1/weeks/2026/regular_season/01",
        "/api/v1/weeks/2026/regular_season/1.0",
        "/api/v1/seasons/%20",
    ],
)
def test_numeric_paths_require_positive_decimal_syntax(path):
    service = FakeService()
    response = client(service).get(path)

    assert response.status_code == 422
    assert response.json() == {"detail": "invalid request"}
    assert response.headers["cache-control"] == "no-store"
    assert service.reads == []


def test_framework_path_validation_has_stable_error_shape():
    response = client(FakeService()).get(
        "/api/v1/weeks/2026/not-a-phase/1"
    )

    assert response.status_code == 422
    assert response.json() == {"detail": "invalid request"}
    assert response.headers["cache-control"] == "no-store"


def test_repository_failure_is_safe_and_cacheless_with_cors():
    c = client(FakeService(error=GameRepositoryError("internal details")))
    r = c.get('/api/v1/bootstrap', headers={"Origin": "https://app.example"})
    assert r.status_code == 503 and r.json() == {"detail": "snapshot temporarily unavailable"}
    assert "internal details" not in r.text
    assert r.headers["retry-after"] == "30" and r.headers["cache-control"] == "no-store"
    assert r.headers["access-control-allow-origin"] == "https://app.example"
    assert "Retry-After" in r.headers["access-control-expose-headers"]


def test_cors_simple_preflight_and_disallowed_origin():
    c = client(FakeService())
    r = c.get('/api/v1/bootstrap', headers={"Origin": "https://app.example"})
    assert r.headers["access-control-allow-origin"] == "https://app.example"
    assert all(x in r.headers["access-control-expose-headers"] for x in ("ETag", "Cache-Control", "Retry-After"))
    r = c.get('/api/v1/bootstrap', headers={"Origin": "https://app.example", "If-None-Match": '"v1"'})
    assert r.status_code == 304 and r.headers["access-control-allow-origin"] == "https://app.example"
    assert all(x in r.headers["access-control-expose-headers"] for x in ("ETag", "Cache-Control", "Retry-After"))
    r = c.get('/api/v1/bootstrap', headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in r.headers
    r = c.options('/api/v1/bootstrap', headers={"Origin": "https://app.example", "Access-Control-Request-Method": "GET", "Access-Control-Request-Headers": "If-None-Match"})
    assert r.status_code == 200 and "GET" in r.headers["access-control-allow-methods"]
    assert "If-None-Match" in r.headers["access-control-allow-headers"]


def test_rejected_cors_preflight_is_not_cacheable():
    response = client(FakeService()).options(
        "/api/v1/bootstrap",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 400
    assert response.headers["cache-control"] == "no-store"
    assert "access-control-allow-origin" not in response.headers


def test_unexpected_failure_is_safe_cacheless_and_cors_visible():
    response = client(FakeService(error=ValueError("internal details"))).get(
        "/api/v1/bootstrap",
        headers={"Origin": "https://app.example"},
    )

    assert response.status_code == 500
    assert response.json() == {"detail": "internal server error"}
    assert "internal details" not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["access-control-allow-origin"] == "https://app.example"


@pytest.mark.parametrize(
    "value",
    [
        "",
        "*",
        "ftp://app.example",
        "https:///missing-host",
        "https://app.example/path",
        "https://app.example?",
        "https://app.example/?x=1",
        "https://app.example#fragment",
        "https://u:p@app.example",
        "https://*.example",
        "https:// app.example",
        "https://app.example:",
        "https://app.example:bad",
    ],
)
def test_origin_parser_rejects_invalid(value):
    with pytest.raises(RuntimeError): parse_origins(value)


def test_cold_read_runtime_import_excludes_write_modules():
    code = dedent(
        """
        import sys
        import backend.nospoil_nfl.api.handler
        import backend.nospoil_nfl.api.http
        from backend.nospoil_nfl.game.read_repository import DynamoReadRepository

        forbidden = {
            'backend.nospoil_nfl.game.repository',
            'backend.nospoil_nfl.game.rules',
            'backend.nospoil_nfl.game.updates',
            'backend.nospoil_nfl.rating.calculator',
        }
        loaded = sorted(forbidden.intersection(sys.modules))
        assert not loaded, loaded
        assert not hasattr(DynamoReadRepository, 'create_if_absent')
        """
    )

    result = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_exported_lambda_handler_with_injected_mangum(monkeypatch):
    import backend.nospoil_nfl.api.handler as handler
    from backend.nospoil_nfl.api import http
    service = FakeService()
    app = create_app(service, origins=["https://app.example"])
    monkeypatch.setattr(http, "create_app", lambda: app)
    monkeypatch.setattr(handler, "_lambda_handler", None)
    event = {"version": "2.0", "routeKey": "$default", "rawPath": "/api/v1/bootstrap", "rawQueryString": "", "headers": {"origin": "https://app.example"}, "requestContext": {"accountId": "123", "apiId": "api", "domainName": "api.example", "domainPrefix": "api", "http": {"method": "GET", "path": "/api/v1/bootstrap", "protocol": "HTTP/1.1", "sourceIp": "127.0.0.1", "userAgent": "pytest"}, "requestId": "req", "routeKey": "$default", "stage": "$default", "time": "07/Sep/2026:00:00:00 +0000", "timeEpoch": 1780000000000}, "isBase64Encoded": False}
    event["requestContext"]["timeEpoch"] = 1780000000000
    class Context:
        function_name = "test"
        def get_remaining_time_in_millis(self): return 30000
    result = handler.lambda_handler(event, Context())
    assert result["statusCode"] == 200 and '"season":2026' in result["body"]
    assert result["headers"]["etag"] == '"v1"' and result["headers"]["cache-control"] == "public, max-age=0, must-revalidate"
    assert result["headers"]["access-control-allow-origin"] == "https://app.example"


def test_import_handler_does_not_require_runtime_environment(monkeypatch):
    monkeypatch.delenv("NOSPOIL_GAMES_TABLE", raising=False)
    monkeypatch.delenv("NOSPOIL_FRONTEND_ORIGINS", raising=False)
    import backend.nospoil_nfl.api.handler as handler
    assert handler.lambda_handler and handler._lambda_handler is None
