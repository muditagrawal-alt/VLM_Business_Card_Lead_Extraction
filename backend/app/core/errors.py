"""Error handling.

Clients get a consistent shape and a message that names what to do about it.
Unexpected errors are logged with their traceback and reported as a generic
failure, so an internal detail never becomes part of the API.
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.logging import get_logger
from app.services.images import ImageValidationError

log = get_logger(__name__)


class AppError(Exception):
    """An error with a status code and a message intended for the user."""

    def __init__(self, message: str, *, status_code: int = status.HTTP_400_BAD_REQUEST) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class NotFoundError(AppError):
    def __init__(self, what: str) -> None:
        super().__init__(f"{what} was not found", status_code=status.HTTP_404_NOT_FOUND)


def _problem(status_code: int, message: str, **extra: object) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": message, **extra})


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        return _problem(exc.status_code, exc.message)

    @app.exception_handler(ImageValidationError)
    async def _image_error(_: Request, exc: ImageValidationError) -> JSONResponse:
        # 415: the request was well-formed, the payload was not a usable image.
        return _problem(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, str(exc))

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        return _problem(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "the request could not be processed",
            details=[
                {"field": ".".join(str(p) for p in err["loc"]), "problem": err["msg"]}
                for err in exc.errors()
            ],
        )

    @app.exception_handler(HTTPException)
    async def _http_error(_: Request, exc: HTTPException) -> JSONResponse:
        return _problem(exc.status_code, str(exc.detail))

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled_error", path=request.url.path, error=type(exc).__name__)
        return _problem(status.HTTP_500_INTERNAL_SERVER_ERROR, "something went wrong on our side")
