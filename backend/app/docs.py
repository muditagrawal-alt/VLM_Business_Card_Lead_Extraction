"""Self-hosted API documentation.

FastAPI's stock ``/docs`` page loads Swagger UI from a CDN and boots it with an
inline ``<script>``. The Content-Security-Policy the edge sets — ``script-src
'self'`` — refuses both, so in production the page rendered blank. Rather than
loosen the policy for one path, the assets are vendored into the image at build
time and served same-origin, and the boot script is served as a file. The page
then works *with* the policy.

In development the assets may not have been fetched (``make docs-assets``); the
page falls back to the CDN there, which is fine because no policy applies.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Response
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

SWAGGER_UI_VERSION = "5.33.0"
ASSETS_DIR = Path(__file__).parent / "static" / "swagger-ui"
STATIC_ROUTE = "/api/docs-static"
CDN_BASE = f"https://cdn.jsdelivr.net/npm/swagger-ui-dist@{SWAGGER_UI_VERSION}"

_INIT_JS = """\
window.addEventListener("DOMContentLoaded", () => {
  window.ui = SwaggerUIBundle({
    url: "%(openapi_url)s",
    dom_id: "#swagger-ui",
    deepLinking: true,
    layout: "BaseLayout",
    showExtensions: true,
    showCommonExtensions: true,
  });
});
"""

_PAGE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>%(title)s - API</title>
<link rel="stylesheet" href="%(base)s/swagger-ui.css">
<link rel="icon" type="image/png" href="%(base)s/favicon-32x32.png">
</head>
<body>
<div id="swagger-ui"></div>
<script src="%(base)s/swagger-ui-bundle.js"></script>
<script src="%(docs_url)s/init.js"></script>
</body>
</html>
"""


def assets_available() -> bool:
    return (ASSETS_DIR / "swagger-ui-bundle.js").is_file()


def install_docs(app: FastAPI, *, docs_url: str, openapi_url: str) -> None:
    """Register the documentation page and its assets on ``app``.

    The app must have been created with ``docs_url=None`` so FastAPI's own
    page does not shadow this one.
    """
    if assets_available():
        app.mount(STATIC_ROUTE, StaticFiles(directory=ASSETS_DIR), name="docs-static")
        base = STATIC_ROUTE
    else:
        base = CDN_BASE

    page = _PAGE % {"title": app.title, "base": base, "docs_url": docs_url}
    init_js = _INIT_JS % {"openapi_url": openapi_url}

    @app.get(docs_url, include_in_schema=False)
    def docs_page() -> HTMLResponse:
        return HTMLResponse(page)

    @app.get(f"{docs_url}/init.js", include_in_schema=False)
    def docs_init() -> Response:
        return Response(init_js, media_type="application/javascript")
