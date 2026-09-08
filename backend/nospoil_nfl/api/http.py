"""Thin, read-only HTTP transport for the provider-free snapshot service."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from urllib.parse import urlparse
from typing import Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware

from ..game.dynamodb_repository import DynamoGameRepository
from ..game.models import DomainValidationError, SeasonPhase
from ..game.repository import GameRepository, GameRepositoryDataError, GameRepositoryError
from .calendar import SeasonCalendar, UnknownSeasonError, UnknownSeasonWeekError
from .snapshots import ReadSnapshotService


def parse_origins(value: str | None) -> list[str]:
    if not value or not value.strip():
        raise RuntimeError("NOSPOIL_FRONTEND_ORIGINS is required")
    origins = [part.strip() for part in value.split(",")]
    if any(not origin for origin in origins):
        raise RuntimeError("NOSPOIL_FRONTEND_ORIGINS contains an empty origin")
    for origin in origins:
        parsed = urlparse(origin)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise RuntimeError("NOSPOIL_FRONTEND_ORIGINS must contain absolute HTTP origins")
        if parsed.path or parsed.params or parsed.query or parsed.fragment:
            raise RuntimeError("NOSPOIL_FRONTEND_ORIGINS cannot contain a path or query")
        if parsed.username or parsed.password:
            raise RuntimeError("NOSPOIL_FRONTEND_ORIGINS cannot contain credentials")
        if origin == "*":
            raise RuntimeError("NOSPOIL_FRONTEND_ORIGINS does not allow wildcard origins")
    return origins


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
    table = boto3.resource("dynamodb", **kwargs).Table(table_name)
    repository = DynamoGameRepository(
        table, index_name=os.environ.get("NOSPOIL_SCHEDULE_INDEX", "season-schedule-index")
    )
    return ReadSnapshotService(repository, SeasonCalendar(), lambda: datetime.now(UTC))


class _ErrorHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable):
        response = await call_next(request)
        if response.status_code >= 400:
            response.headers["Cache-Control"] = "no-store"
        return response


def _etag_matches(value: str | None, current: str) -> bool:
    if not value:
        return False
    return any(token.strip() == "*" or token.strip().removeprefix("W/") == current for token in value.split(","))


def _success_headers(etag: str) -> dict[str, str]:
    return {"ETag": etag, "Cache-Control": "public, max-age=0, must-revalidate"}


def create_app(
    service: ReadSnapshotService | None = None,
    *,
    repository: GameRepository | None = None,
    calendar: SeasonCalendar | None = None,
    clock: object | None = None,
    origins: list[str] | None = None,
) -> FastAPI:
    """Create the ASGI app; dependencies may be injected for tests."""
    if service is None:
        if repository is None:
            service = build_service_from_environment()
        else:
            service = ReadSnapshotService(
                repository, calendar or SeasonCalendar(), clock or (lambda: datetime.now(UTC))
            )
    allowed_origins = (
        parse_origins(os.environ.get("NOSPOIL_FRONTEND_ORIGINS"))
        if origins is None
        else parse_origins(",".join(origins))
    )
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(_ErrorHeadersMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_methods=["GET", "HEAD", "OPTIONS"],
        allow_headers=["If-None-Match"],
        expose_headers=["ETag", "Cache-Control", "Retry-After"],
        allow_credentials=False,
    )

    async def reply(request: Request, result) -> Response:
        headers = _success_headers(result.etag)
        if _etag_matches(request.headers.get("if-none-match"), result.etag):
            return Response(status_code=304, headers=headers)
        if request.method == "HEAD":
            return Response(status_code=200, headers=headers)
        return JSONResponse(result.body, headers=headers)

    async def repository_error(request: Request, exc: Exception):
        return JSONResponse({"detail": "snapshot temporarily unavailable"}, status_code=503, headers={"Retry-After": "30", "Cache-Control": "no-store"})

    app.add_exception_handler(GameRepositoryError, repository_error)
    app.add_exception_handler(GameRepositoryDataError, repository_error)

    @app.exception_handler(DomainValidationError)
    async def validation_error(request: Request, exc: Exception):
        return JSONResponse({"detail": str(exc)}, status_code=422)


    @app.exception_handler(UnknownSeasonError)
    async def unknown_season(request: Request, exc: Exception):
        return JSONResponse({"detail": "unknown season"}, status_code=404)

    @app.exception_handler(UnknownSeasonWeekError)
    async def unknown_week(request: Request, exc: Exception):
        return JSONResponse({"detail": "unknown season week"}, status_code=404)

    @app.api_route("/api/v1/bootstrap", methods=["GET", "HEAD"])
    async def bootstrap(request: Request):
        return await reply(request, service.bootstrap())

    @app.api_route("/api/v1/weeks/{season}/{phase}/{week}", methods=["GET", "HEAD"])
    async def week(request: Request, season: int, phase: SeasonPhase, week: int):
        return await reply(request, service.week_snapshot(season, phase, week))

    @app.api_route("/api/v1/seasons/{season}", methods=["GET", "HEAD"])
    async def season(request: Request, season: int):
        return await reply(request, service.season_snapshot(season))

    return app


__all__ = ["build_service_from_environment", "create_app", "parse_origins"]
