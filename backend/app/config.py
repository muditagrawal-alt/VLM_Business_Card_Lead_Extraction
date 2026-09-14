"""Application settings.

Every tunable is an environment variable with a safe default, so the same image
runs locally, on the GPU box and on the CPU-profile box with only `.env` changing.
See `.env.example` for the documented set.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, PostgresDsn, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ProviderSettings(BaseSettings):
    """One inference tier in the provider chain."""

    enabled: bool = True
    base_url: str = ""
    model: str = ""
    api_key: str = ""
    timeout_s: float = 60.0


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---- App ----
    app_env: Literal["development", "production"] = "development"
    app_port: int = 8000
    app_base_url: str = "http://localhost:5173"
    log_level: str = "INFO"
    app_access_code: str = ""

    # ---- Database ----
    database_url: PostgresDsn | str = "postgresql+asyncpg://leads:leads@localhost:5432/leads"

    # ---- Storage ----
    storage_backend: Literal["local", "s3"] = "local"
    storage_local_path: Path = Path("./storage")
    s3_bucket: str = ""
    aws_region: str = "us-east-1"

    # ---- Upload limits ----
    max_files_per_job: int = 50
    max_file_size_mb: int = 10
    image_max_edge_px: int = 768

    # ---- Retention ----
    retention_days: int = 7

    # ---- Tier 1: GPU (llama.cpp CUDA/Metal) ----
    vlm_gpu_enabled: bool = True
    vlm_gpu_base_url: str = "http://localhost:8080/v1"
    vlm_gpu_model: str = "Qwen3VL-8B-Instruct-Q8_0"
    vlm_gpu_timeout_s: float = 60.0

    # ---- Tier 2: CPU (llama.cpp CPU build) ----
    vlm_cpu_enabled: bool = True
    vlm_cpu_base_url: str = "http://localhost:8081/v1"
    vlm_cpu_model: str = "Qwen3VL-4B-Instruct-Q4_K_M"
    vlm_cpu_timeout_s: float = 180.0

    # ---- Tier 3: hosted Qwen (Alibaba Model Studio) ----
    # Enabling this means card images leave our server; disclosed in the UI.
    vlm_cloud_enabled: bool = True
    vlm_cloud_base_url: str = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
    vlm_cloud_model: str = "qwen3-vl-plus"
    vlm_cloud_api_key: str = ""
    vlm_cloud_timeout_s: float = 90.0

    # ---- Circuit breaker ----
    breaker_failure_threshold: int = 3
    breaker_reset_seconds: float = 60.0
    provider_health_interval_s: float = 15.0

    # ---- Worker ----
    worker_concurrency: int = Field(default=2, ge=1, le=16)
    task_max_attempts: int = Field(default=2, ge=1, le=10)
    task_lease_seconds: int = 300

    # ---- Rate limits ----
    rate_limit_jobs: str = "5/10minutes"
    rate_limit_images_per_hour: int = 100

    @field_validator("database_url", mode="after")
    @classmethod
    def _require_async_driver(cls, v: PostgresDsn | str) -> str:
        """Fail fast on a sync driver — the whole data layer is async."""
        url = str(v)
        if url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        return url

    @property
    def max_file_size_bytes(self) -> int:
        return self.max_file_size_mb * 1024 * 1024

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    def provider_chain(self) -> list[tuple[str, ProviderSettings]]:
        """Inference tiers in fallback order: GPU -> CPU -> hosted."""
        return [
            (
                "gpu",
                ProviderSettings(
                    enabled=self.vlm_gpu_enabled,
                    base_url=self.vlm_gpu_base_url,
                    model=self.vlm_gpu_model,
                    timeout_s=self.vlm_gpu_timeout_s,
                ),
            ),
            (
                "cpu",
                ProviderSettings(
                    enabled=self.vlm_cpu_enabled,
                    base_url=self.vlm_cpu_base_url,
                    model=self.vlm_cpu_model,
                    timeout_s=self.vlm_cpu_timeout_s,
                ),
            ),
            (
                "cloud",
                ProviderSettings(
                    # A cloud tier without a key is not usable; treat as disabled.
                    enabled=self.vlm_cloud_enabled and bool(self.vlm_cloud_api_key),
                    base_url=self.vlm_cloud_base_url,
                    model=self.vlm_cloud_model,
                    api_key=self.vlm_cloud_api_key,
                    timeout_s=self.vlm_cloud_timeout_s,
                ),
            ),
        ]


@lru_cache
def get_settings() -> Settings:
    return Settings()
