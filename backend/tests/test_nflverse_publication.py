from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
import requests

from backend.nospoil_nfl.providers.nflverse_publication import (
    NflversePublicationClient,
    Publication,
    PublicationUnavailable,
)


NOW = datetime(2026, 10, 27, 12, tzinfo=UTC)


class Session:
    def __init__(self, status=200, headers=None):
        self.response = SimpleNamespace(
            status_code=status, headers=headers or {}, close=self.close
        )
        self.closed = False
        self.calls = []

    def head(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response

    def close(self):
        self.closed = True


def test_exact_asset_head_follows_redirects_sends_validator_and_closes_response():
    session = Session(304)
    previous = Publication('"v1"', '"v1"', NOW.isoformat())
    result = NflversePublicationClient(session=session, timeout_seconds=3).probe(
        2026, previous, now=NOW
    )
    assert result == previous
    url, args = session.calls[0]
    assert url.endswith("/pbp/play_by_play_2026.parquet")
    assert args["headers"]["If-None-Match"] == '"v1"'
    assert args["allow_redirects"] is True and args["timeout"] == 3
    assert session.closed


def test_new_publication_normalizes_modified_time_and_uses_etag():
    session = Session(
        headers={"ETag": '"new"', "Last-Modified": "Tue, 27 Oct 2026 09:15:00 GMT"}
    )
    publication = NflversePublicationClient(session=session).probe(
        2026, Publication(None), now=NOW
    )
    assert publication == Publication('"new"', '"new"', "2026-10-27T09:15:00+00:00")


def test_missing_etag_uses_timestamp_and_size_without_downloading():
    session = Session(
        headers={
            "Last-Modified": "Tue, 27 Oct 2026 09:15:00 GMT",
            "Content-Length": "123",
        }
    )
    publication = NflversePublicationClient(session=session).probe(
        2026, Publication(None), now=NOW
    )
    assert publication.revision == "2026-10-27T09:15:00+00:00/123"
    assert publication.etag is None


@pytest.mark.parametrize("status", [405, 501])
def test_unsupported_head_requests_bounded_payload_fallback(status):
    session = Session(status)
    assert (
        NflversePublicationClient(session=session)
        .probe(2026, Publication(None), now=NOW)
        .revision
        is None
    )


@pytest.mark.parametrize("status", [403, 404, 429, 500, 503])
def test_http_errors_are_never_treated_as_unchanged_or_payload_fallback(status):
    session = Session(status, {"Retry-After": "3600"})
    with pytest.raises(PublicationUnavailable) as error:
        NflversePublicationClient(session=session).probe(
            2026, Publication("old"), now=NOW
        )
    assert error.value.retry_after == 3600
    assert session.closed


def test_retry_after_http_date():
    session = Session(429, {"Retry-After": "Tue, 27 Oct 2026 13:00:00 GMT"})
    with pytest.raises(PublicationUnavailable) as error:
        NflversePublicationClient(session=session).probe(
            2026, Publication(None), now=NOW
        )
    assert error.value.retry_after == 3600


def test_transport_error_is_not_unchanged():
    def fail(*args, **kwargs):
        raise requests.Timeout()

    with pytest.raises(PublicationUnavailable):
        NflversePublicationClient(session=SimpleNamespace(head=fail)).probe(
            2026, Publication("old"), now=NOW
        )


def test_malformed_last_modified_does_not_disable_etag_gate():
    session = Session(headers={"ETag": "v1", "Last-Modified": "bad date"})
    publication = NflversePublicationClient(session=session).probe(
        2026, Publication(None), now=NOW
    )
    assert publication == Publication("v1", "v1", None)


def test_schedule_uses_its_own_exact_asset():
    session = Session(headers={"ETag": "schedule-v1"})
    NflversePublicationClient(session=session).probe_schedule(
        Publication(None), now=NOW
    )
    assert session.calls[0][0].endswith("/schedules/games.parquet")
