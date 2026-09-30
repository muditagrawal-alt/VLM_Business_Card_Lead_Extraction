"""Every setting an operator can change must actually reach the containers.

The production compose file lists the application's environment explicitly,
so a setting added to Settings but not to that list is silently fixed at its
default in production, whatever .env says. That happened twice: first to the
hosted tier's retry and budget settings, then to APP_CORS_ORIGINS, which left a
separately hosted frontend unable to call the API at all. It had also quietly
kept WORKER_CONCURRENCY at 2 on a two-vCPU CPU deployment configured for 1.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.config import Settings

COMPOSE = Path(__file__).parents[3] / "deploy" / "docker-compose.prod.yml"

# Settings deliberately not configurable in production, and why.
NOT_PASSED = {
    "APP_PORT": "uvicorn's port is fixed in the api command",
    "APP_BASE_URL": "only used to allow the Vite dev server in development",
    "STORAGE_BACKEND": "production storage is the compose volume",
    "S3_BUCKET": "production storage is the compose volume",
    "AWS_REGION": "production storage is the compose volume",
}


def passed_to_containers() -> set[str]:
    text = COMPOSE.read_text()
    anchor = text[text.index("x-app-env: &app-env") : text.index("\nservices:")]
    return set(re.findall(r"^\s+([A-Z][A-Z0-9_]+):", anchor, re.MULTILINE))


def test_every_setting_reaches_the_containers() -> None:
    settings = {name.upper() for name in Settings.model_fields}
    missing = settings - passed_to_containers() - set(NOT_PASSED)
    assert not missing, (
        f"{sorted(missing)} can be set in .env but never reach the api and worker "
        "containers; add them to x-app-env in deploy/docker-compose.prod.yml, or to "
        "NOT_PASSED here with the reason"
    )


def test_the_exceptions_are_still_real_settings() -> None:
    """An exception for a setting that no longer exists is dead weight."""
    settings = {name.upper() for name in Settings.model_fields}
    assert set(NOT_PASSED) <= settings
