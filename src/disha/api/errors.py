"""RFC 7807 Problem Details response model."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel


class Problem(BaseModel):
    type: str = "about:blank"
    title: str
    status: int
    detail: str
    instance: str | None = None


def _problem_response(
    status: int,
    title: str,
    detail: str,
    *,
    instance: str | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content=Problem(title=title, status=status, detail=detail, instance=instance).model_dump(),
        media_type="application/problem+json",
        headers=headers,
    )


def register_handlers(app: FastAPI) -> None:
    @app.exception_handler(HTTPException)
    async def _http_exc(request: Request, exc: HTTPException) -> JSONResponse:
        return _problem_response(
            status=exc.status_code,
            title=exc.detail if isinstance(exc.detail, str) else "HTTP error",
            detail=str(exc.detail),
            instance=str(request.url.path),
            headers=dict(exc.headers) if exc.headers else None,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_exc(request: Request, exc: RequestValidationError) -> JSONResponse:
        return _problem_response(
            status=422,
            title="Validation Error",
            detail=str(exc.errors()),
            instance=str(request.url.path),
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        import logging

        logging.getLogger("disha.api").exception("unhandled exception")
        return _problem_response(
            status=500,
            title="Internal Server Error",
            detail=f"{type(exc).__name__}: {exc}",
            instance=str(request.url.path),
        )
