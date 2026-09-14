# Business Card Lead Extraction with a Self-Hosted Qwen VLM

Upload business card images in bulk, extract structured lead data with a
self-hosted **Qwen3-VL** vision-language model, review the results in the
browser, and download them as a formatted Excel workbook.

> **Live URL:** _added when deployed_

## Extracted fields

First Name · Last Name · Position / Job Title · Company · Location ·
Phone Number · Email Address

Additional data captured per card: website, full address breakdown, secondary
phones and emails, raw transcribed text, and a per-field confidence score.

## How it works

Cards are never sent to a single point of failure. Each image runs through an
ordered chain of inference tiers, each behind its own circuit breaker:

| Tier | Model | Where | Typical latency |
|------|-------|-------|-----------------|
| 1 | Qwen3-VL-8B-Instruct (Q8_0) | Self-hosted, GPU | ~2–4 s / card |
| 2 | Qwen3-VL-4B-Instruct (Q4_K_M) | Self-hosted, CPU | ~30–60 s / card |
| 3 | Qwen3-VL (hosted Qwen API) | Alibaba Model Studio | ~3–6 s / card |

Output is **grammar-constrained to a JSON schema** on the self-hosted tiers, so
the model cannot emit malformed JSON or invent fields. Extracted values are then
normalised (phone numbers to E.164, emails validated, names split and cleaned)
before being stored.

No separate OCR engine is used — Qwen3-VL reads the card text directly. The
reasoning behind that and every other significant decision is recorded in
[docs/DECISIONS.md](docs/DECISIONS.md).

## Tech stack

- **Backend** — Python 3.12, FastAPI, SQLAlchemy 2 (async), Alembic, PostgreSQL 16
- **Inference** — llama.cpp `llama-server`, Qwen3-VL GGUF weights
- **Frontend** — React 19, TypeScript, Vite, Tailwind CSS 4, TanStack Query/Table
- **Export** — openpyxl (styled `.xlsx`) and CSV
- **Infrastructure** — Docker Compose, Caddy (automatic HTTPS), AWS EC2

## Quick start

```bash
make install        # backend + frontend dependencies
make models         # download Qwen3-VL GGUF weights (~14 GB)
make db-up          # start PostgreSQL
make migrate        # apply schema
make llama-gpu      # terminal 2: serve the 8B model
make dev            # terminal 3: API + worker + frontend
```

Then open http://localhost:5173.

## Documentation

| Document | Contents |
|---|---|
| [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md) | Full design, phased build plan, risks |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Component and request-flow diagrams |
| [docs/DECISIONS.md](docs/DECISIONS.md) | Why each technology was chosen |
| [docs/RUNBOOK.md](docs/RUNBOOK.md) | Deploy, roll back, restore, switch tiers |
| [docs/EVALUATION.md](docs/EVALUATION.md) | Accuracy and latency measurements |

## Licence

MIT
