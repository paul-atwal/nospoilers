"""Thin, read-only HTTP transport for the provider-free snapshot service.

Plain Starlette rather than FastAPI: the API has three GET routes and no
request bodies, and importing FastAPI (and pydantic with it) roughly doubled
the read Lambda's cold start.
"""

from __future__ import annotations

import os
import re
from datetime import UTC, datetime
from ipaddress import IPv4Address, IPv6Address
from typing import Callable
from urllib.parse import urlparse

from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from ..game.models import DomainValidationError, SeasonPhase
from ..game.read_repository import (
    DynamoReadRepository,
    GameReadRepository,
    GameRepositoryDataError,
    GameRepositoryError,
)
from .calendar import SeasonCalendar, UnknownSeasonError, UnknownSeasonWeekError
from .snapshots import ReadSnapshotService


def _canonical_origin(origin: str) -> str:
    parsed = urlparse(origin)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.path
        or parsed.params
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
        or any(character.isspace() for character in origin)
        or any(character in parsed.netloc for character in "@%*\\")
    ):
        raise ValueError

    hostname = parsed.hostname
    hostname.encode("ascii")
    if ":" in hostname:
        canonical_host = f"[{IPv6Address(hostname).compressed}]"
    else:
        if re.fullmatch(r"[0-9.]+", hostname):
            canonical_host = str(IPv4Address(hostname))
        else:
            labels = hostname.split(".")
            if any(
                not label
                or len(label) > 63
                or not re.fullmatch(r"[a-z0-9-]+", label)
                or label.startswith("-")
                or label.endswith("-")
                for label in labels
            ):
                raise ValueError
            canonical_host = hostname

    port = parsed.port
    default_port = 80 if parsed.scheme == "http" else 443
    canonical_port = "" if port is None or port == default_port else f":{port}"
    return f"{parsed.scheme}://{canonical_host}{canonical_port}"


def parse_origins(value: str | None) -> list[str]:
    if not value or not value.strip():
        raise RuntimeError("NOSPOIL_FRONTEND_ORIGINS is required")
    origins = [part.strip() for part in value.split(",")]
    if any(not origin for origin in origins):
        raise RuntimeError("NOSPOIL_FRONTEND_ORIGINS contains an empty origin")
    for origin in origins:
        try:
            if _canonical_origin(origin) != origin:
                raise ValueError
        except (UnicodeError, ValueError) as error:
            raise RuntimeError(
                "NOSPOIL_FRONTEND_ORIGINS contains an invalid origin"
            ) from error
    return origins


def _dynamodb_config():
    from botocore.config import Config

    return Config(
        connect_timeout=2,
        read_timeout=5,
        retries={"mode": "standard", "total_max_attempts": 2},
    )


def build_service_from_environment() -> ReadSnapshotService:
    table_name = os.environ.get("NOSPOIL_GAMES_TABLE")
    if not table_name:
        raise RuntimeError("NOSPOIL_GAMES_TABLE is required")
    import boto3

    kwargs: dict[str, object] = {}
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
    if region:
        kwargs["region_name"] = region
    endpoint = os.environ.get("NOSPOIL_DYNAMODB_LOCAL_ENDPOINT")
    if endpoint:
        kwargs["endpoint_url"] = endpoint
    kwargs["config"] = _dynamodb_config()
    table = boto3.resource("dynamodb", **kwargs).Table(table_name)
    repository = DynamoReadRepository(
        table,
        index_name=os.environ.get("NOSPOIL_SCHEDULE_INDEX", "season-schedule-index"),
    )
    return ReadSnapshotService(repository, SeasonCalendar(), lambda: datetime.now(UTC))


class _ErrorHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable):
        response = await call_next(request)
        if response.status_code >= 400:
            response.headers["Cache-Control"] = "no-store"
        return response


class _UnexpectedMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable):
        try:
            return await call_next(request)
        except Exception:
            return JSONResponse({"detail": "internal server error"}, status_code=500)


def _etag_matches(value: str | None, current: str) -> bool:
    if not value:
        return False
    return any(
        token.strip() == "*" or token.strip().removeprefix("W/") == current
        for token in value.split(",")
    )


def _success_headers(etag: str) -> dict[str, str]:
    return {"ETag": etag, "Cache-Control": "public, max-age=0, must-revalidate"}


def _parse_path_int(value: str) -> int:
    if len(value) > 10 or not re.fullmatch(r"[1-9][0-9]*", value):
        raise DomainValidationError("invalid request")
    parsed = int(value)
    if parsed > 2_147_483_647:
        raise DomainValidationError("invalid request")
    return parsed


def _parse_phase(value: str) -> SeasonPhase:
    try:
        return SeasonPhase(value)
    except ValueError:
        raise DomainValidationError("invalid request") from None


def create_app(
    service: ReadSnapshotService | None = None,
    *,
    repository: GameReadRepository | None = None,
    calendar: SeasonCalendar | None = None,
    clock: object | None = None,
    origins: list[str] | None = None,
) -> Starlette:
    """Create the ASGI app; dependencies may be injected for tests."""
    if service is None:
        if repository is None:
            service = build_service_from_environment()
        else:
            service = ReadSnapshotService(
                repository,
                calendar or SeasonCalendar(),
                clock or (lambda: datetime.now(UTC)),
            )
    allowed_origins = (
        parse_origins(os.environ.get("NOSPOIL_FRONTEND_ORIGINS"))
        if origins is None
        else parse_origins(",".join(origins))
    )
    async def reply(request: Request, result) -> Response:
        headers = _success_headers(result.etag)
        if _etag_matches(request.headers.get("if-none-match"), result.etag):
            return Response(status_code=304, headers=headers)
        if request.method == "HEAD":
            return Response(status_code=200, headers=headers)
        return JSONResponse(result.body, headers=headers)

    async def http_error(request: Request, exc: HTTPException):
        # Match FastAPI's JSON body for routing errors such as 404 and 405.
        return JSONResponse(
            {"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers
        )

    async def repository_error(request: Request, exc: Exception):
        return JSONResponse(
            {"detail": "snapshot temporarily unavailable"},
            status_code=503,
            headers={"Retry-After": "30", "Cache-Control": "no-store"},
        )

    async def validation_error(request: Request, exc: Exception):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    async def unknown_season(request: Request, exc: Exception):
        return JSONResponse({"detail": "unknown season"}, status_code=404)

    async def unknown_week(request: Request, exc: Exception):
        return JSONResponse({"detail": "unknown season week"}, status_code=404)

    async def bootstrap(request: Request):
        return await reply(request, service.bootstrap())

    async def week(request: Request):
        params = request.path_params
        return await reply(
            request,
            service.week_snapshot(
                _parse_path_int(params["season"]),
                _parse_phase(params["phase"]),
                _parse_path_int(params["week"]),
            ),
        )

    async def season(request: Request):
        return await reply(
            request, service.season_snapshot(_parse_path_int(request.path_params["season"]))
        )

    methods = ["GET", "HEAD"]
    return Starlette(
        routes=[
            Route("/api/v1/bootstrap", bootstrap, methods=methods),
            Route("/api/v1/weeks/{season}/{phase}/{week}", week, methods=methods),
            Route("/api/v1/seasons/{season}", season, methods=methods),
        ],
        # Outermost first: error headers wrap CORS, which wraps the 500 guard.
        middleware=[
            Middleware(_ErrorHeadersMiddleware),
            Middleware(
                CORSMiddleware,
                allow_origins=allowed_origins,
                allow_methods=["GET", "HEAD", "OPTIONS"],
                allow_headers=["If-None-Match"],
                expose_headers=["ETag", "Cache-Control", "Retry-After"],
                allow_credentials=False,
            ),
            Middleware(_UnexpectedMiddleware),
        ],
        exception_handlers={
            HTTPException: http_error,
            GameRepositoryError: repository_error,
            GameRepositoryDataError: repository_error,
            DomainValidationError: validation_error,
            UnknownSeasonError: unknown_season,
            UnknownSeasonWeekError: unknown_week,
        },
    )


__all__ = ["build_service_from_environment", "create_app", "parse_origins"]
