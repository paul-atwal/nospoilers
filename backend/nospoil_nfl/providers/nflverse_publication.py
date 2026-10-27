"""Payload-free checks of exact nflverse assets, rather than a release tag."""

from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import requests

from .errors import ProviderUnavailableError


BASE_URL = "https://github.com/nflverse/nflverse-data/releases/download"


@dataclass(frozen=True)
class Publication:
    revision: str | None
    etag: str | None = None
    modified: str | None = None


class PublicationUnavailable(ProviderUnavailableError):
    def __init__(self, retry_after: int = 0) -> None:
        super().__init__(
            "nflverse publication check failed",
            provider="nflverse",
            operation="publication",
        )
        self.retry_after = retry_after


class NflversePublicationClient:
    def __init__(self, *, timeout_seconds: float = 20, session=None) -> None:
        self._timeout = timeout_seconds
        self._session = session or requests.Session()

    def probe(
        self, season: int, previous: Publication, *, now: datetime
    ) -> Publication:
        return self._probe(
            f"{BASE_URL}/pbp/play_by_play_{season}.parquet", previous, now
        )

    def probe_schedule(self, previous: Publication, *, now: datetime) -> Publication:
        return self._probe(f"{BASE_URL}/schedules/games.parquet", previous, now)

    def _probe(self, url: str, previous: Publication, now: datetime) -> Publication:
        headers = {"User-Agent": "nospoil-nfl/confirmation"}
        if previous.etag:
            headers["If-None-Match"] = previous.etag
        try:
            response = self._session.head(
                url, headers=headers, timeout=self._timeout, allow_redirects=True
            )
        except requests.RequestException as error:
            raise PublicationUnavailable() from error
        try:
            if response.status_code == 304:
                if previous.revision is None:
                    raise PublicationUnavailable()
                return previous
            if response.status_code in {405, 501}:
                return Publication(None)
            if response.status_code != 200:
                retry = response.headers.get("Retry-After", "0")
                try:
                    delay = int(retry)
                except ValueError:
                    try:
                        delay = int(
                            (parsedate_to_datetime(retry) - now).total_seconds()
                        )
                    except (ValueError, TypeError, OverflowError):
                        delay = 0
                raise PublicationUnavailable(max(0, delay))
            etag = response.headers.get("ETag")
            modified = response.headers.get("Last-Modified")
            try:
                modified = (
                    parsedate_to_datetime(modified).astimezone(UTC).isoformat()
                    if modified
                    else None
                )
            except (ValueError, TypeError, OverflowError):
                modified = None
            # Last-Modified plus size is a fallback validator, never a claim
            # about the completeness of an individual game's PBP.
            revision = etag or (
                f"{modified}/{response.headers.get('Content-Length')}"
                if modified
                else None
            )
            return Publication(revision, etag, modified)
        finally:
            response.close()
