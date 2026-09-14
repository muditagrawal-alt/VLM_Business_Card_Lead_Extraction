# Backend — VLM Lead Extraction API

FastAPI service + worker that turn business card images into structured leads
using a self-hosted Qwen Vision-Language Model.

```
app/
  api/       HTTP routes (jobs, leads, export, images, health)
  core/      db session, logging, rate limiting, error handlers
  models/    SQLAlchemy ORM models
  schemas/   Pydantic models — single source of truth for the VLM JSON schema,
             the API contract and the Excel columns
  services/  images, extraction/normalisation, export, storage, retention
  vlm/       provider chain: GPU 8B -> CPU 4B -> hosted Qwen, with breakers
  worker/    queue poller (Postgres SKIP LOCKED) that runs extraction
```

See [../docs/IMPLEMENTATION_PLAN.md](../docs/IMPLEMENTATION_PLAN.md) for the full design.

## Local development

```bash
uv venv --python 3.12
uv pip install -e ".[dev]"
uv run pytest            # tests
uv run ruff check .      # lint
uv run pyright           # types
```
