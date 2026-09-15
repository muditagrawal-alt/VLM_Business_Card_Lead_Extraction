<div align="center">

# Business Card Lead Extraction

**Turn a pile of business cards into a clean, verified lead list — using a self-hosted vision-language model.**

Upload cards in bulk · extract seven structured fields per card · review and correct in the browser · export a formatted Excel workbook.

[Live demo](#) · [Architecture](docs/ARCHITECTURE.md) · [Design decisions](docs/DECISIONS.md) · [Evaluation](docs/EVALUATION.md) · [Runbook](docs/RUNBOOK.md)

</div>

![The results view, showing extracted leads with per-field confidence and the inference tier that produced each row](docs/images/results-light.png)

---

## Contents

- [What it does](#what-it-does)
- [How it works](#how-it-works)
- [Measured results](#measured-results)
- [Screenshots](#screenshots)
- [Quick start](#quick-start)
- [Configuration](#configuration)
- [API](#api)
- [Testing](#testing)
- [Deployment](#deployment)
- [Project structure](#project-structure)
- [Known limits](#known-limits)
- [Privacy and data handling](#privacy-and-data-handling)

---

## What it does

Sales teams come back from a conference with fifty business cards and no way to get them into a CRM except by typing. This tool reads them.

**Extracted per card:** First Name · Last Name · Position · Company · Location · Phone · Email

**Also captured:** website, the postal address split into parts, secondary phones and emails, the full text the model read, and a per-field confidence score.

Three properties make the output usable rather than merely plausible:

| | |
|---|---|
| **It never invents a value** | Output is constrained to a schema where every field may be `null`. A card with no job title returns no job title — flagged for review, not filled with something plausible. |
| **Values are normalised, not just transcribed** | Phone numbers become E.164, emails are validated, honorifics and suffixes are stripped from names, and one primary phone is chosen from however many the card lists. |
| **Every row is attributable** | Each lead records which model read it, how it was decoded, and how long it took. A batch that spanned inference tiers is never presented as though it did not. |

## How it works

```
upload → validate & deduplicate → EXIF orient, strip metadata, downscale
       → queue (PostgreSQL) → worker → VLM → normalise → lead → Excel
```

A card is never sent to a single point of failure. Each one runs through an ordered chain of inference tiers, each behind its own circuit breaker:

| Tier | Model | Where | Latency/card |
|---|---|---|---|
| 1 | Qwen3-VL-8B-Instruct (Q8_0) | Self-hosted, NVIDIA T4 | ~2–4 s* |
| 2 | Qwen3-VL-4B-Instruct (Q4_K_M) | Self-hosted, CPU | ~20–30 s |
| 3 | Qwen3-VL (hosted Qwen API) | Alibaba Model Studio | ~3–6 s |

<sub>*Projected for a T4 and not yet measured on one; see [Evaluation](docs/EVALUATION.md).</sub>

If a tier fails or times out, the next takes over. After three consecutive failures its breaker opens and it is skipped until a probe succeeds — without that, a dead GPU container would cost every card in a 50-card batch a full timeout before falling through.

### Structured output

On the self-hosted tiers the JSON schema is compiled into a grammar, so the model **cannot** emit malformed JSON or unknown fields. Hosted providers vary, so the client degrades through `json_schema` → `json_object` → mining the first JSON object out of the text, with one repair attempt that feeds the validation error back. The rung that succeeded is stored per card, which keeps accuracy comparisons between tiers honest.

Every field is **required but nullable**: the model must answer for each one, and an explicit `null` is a valid answer. That distinction is what took field accuracy from 82.1% to 100% on the evaluation set — an *optional* field in a grammar is one the model may silently skip, and it did.

### No separate OCR engine

Qwen3-VL reads card text directly and more accurately than a CRAFT/CRNN pipeline; feeding EasyOCR output into it would add a worse signal to a better reader. Docling is a PDF/DOCX layout converter that delegates to an OCR engine for images, so "Docling + EasyOCR" is EasyOCR plus a large dependency tree — and EasyOCR pulls in PyTorch, ~800 MB of RAM better spent on a larger model. Full reasoning in [docs/DECISIONS.md](docs/DECISIONS.md).

## Measured results

Every figure here comes from `eval/run_eval.py`, not from impressions.

| Model | Image | Field accuracy | Perfect cards | p50 | p95 |
|---|---|---|---|---|---|
| Qwen3-VL-4B Q4_K_M | 768 px | **100.0%** | 8 / 8 | 20.6 s | 30.1 s |
| Qwen3-VL-8B Q8_0 | 768 px | **100.0%** | 8 / 8 | 64.4 s | 72.8 s |

Measured on Apple Silicon with Metal offload, against eight synthetic cards built around the layouts that break extraction: a dark centred card, one with no job title, a first name given only as an initial, an honorific and suffix, two people on one card, a slogan where a company name usually sits, and a card listing mobile, office and fax.

> **This is not a production accuracy claim.** Those cards are clean renders with perfect focus and no glare, skew or creases. They isolate reasoning and schema failures — which is what they were built for, and they caught a real one — but they do not test perception. Both models now score full marks, so the set can no longer distinguish them; harder input is the next evaluation priority. See [docs/EVALUATION.md](docs/EVALUATION.md) for what remains unmeasured.

## Screenshots

<table>
<tr>
<td width="50%"><img src="docs/images/upload.png" alt="The upload view with a drag-and-drop area for business card images"></td>
<td width="50%"><img src="docs/images/drawer.png" alt="The detail drawer showing a card image beside its editable extracted fields"></td>
</tr>
<tr>
<td><em>Bulk upload — up to 50 cards, downscaled in the browser before upload.</em></td>
<td><em>Review a card against what the model read, and correct any field.</em></td>
</tr>
<tr>
<td colspan="2"><img src="docs/images/results-dark.png" alt="The results view in dark theme"></td>
</tr>
<tr>
<td colspan="2"><em>Light, dark and system themes; the palette is taken from O-HIVE's own design system.</em></td>
</tr>
</table>

## Quick start

**Requires** Docker, Python 3.12, Node 22, [uv](https://docs.astral.sh/uv/), and [llama.cpp](https://github.com/ggml-org/llama.cpp).

```bash
make install        # backend and frontend dependencies
make models         # Qwen3-VL weights (~13 GB, resumable; TIERS=cpu for just the 4B)
make db-up          # PostgreSQL on port 5433
make migrate
```

Then, in separate terminals:

```bash
make llama-cpu      # serve the 4B model on 127.0.0.1:18081
make dev            # API, worker and frontend
```

Open <http://localhost:5173>. The API documents itself at <http://localhost:8000/api/docs>.

<details>
<summary><strong>Why the unusual ports?</strong></summary>

The development database uses **5433** and the model servers **18080/18081**, rather than the defaults. A locally installed PostgreSQL usually holds 5432 and — being bound to `127.0.0.1` — silently wins the connection over Docker's wildcard bind, producing a confusing "role does not exist" error against the wrong database. Port 8080 is similarly contested (Airflow, Spark and Tomcat all default to it), and pointing the app at an unrelated service produces a failure that looks like a model problem.

Production is unaffected: Compose addresses the model servers by container name on an internal network.
</details>

## Configuration

Every setting is an environment variable with a safe default, so one image runs unchanged across local, GPU and CPU-profile deployments. Copy `.env.example` and edit. The full set is documented there; the ones that matter most:

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | — | PostgreSQL DSN. A sync `postgresql://` scheme is rewritten to `postgresql+asyncpg://` rather than silently blocking the event loop. |
| `VLM_GPU_ENABLED` | `true` | Tier 1. Disable on a CPU-only host so the chain does not spend a timeout on an absent server. |
| `VLM_CLOUD_API_KEY` | — | Tier 3. **A cloud tier with no key is treated as disabled**, so a missing secret degrades to the self-hosted tiers instead of failing every card. |
| `IMAGE_MAX_EDGE_PX` | `768` | Long-edge cap on images sent to the model. The dominant latency lever. |
| `MAX_FILES_PER_JOB` | `50` | Batch ceiling. Exceeding it returns 413 naming the limit. |
| `WORKER_CONCURRENCY` | `2` | Cards in flight. Set to 1 on CPU — llama.cpp on two vCPUs gains nothing from parallel requests. |
| `RETENTION_DAYS` | `7` | After this, batches, leads and images are deleted. |
| `APP_ACCESS_CODE` | *(empty)* | Optional passcode gating the endpoints that cost inference time. |

## API

Interactive documentation at `/api/docs`. All routes are under `/api/v1`.

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/jobs` | Upload 1–50 images. Returns accepted, duplicate and rejected counts. |
| `GET` | `/jobs/{id}` | Batch progress, per-card state, and a completion estimate. |
| `GET` | `/jobs/{id}/leads` | Extracted leads. |
| `PATCH` | `/leads/{id}` | Correct a lead. Only fields sent are changed. |
| `GET` | `/jobs/{id}/export.xlsx` · `.csv` | Download the batch. |
| `POST` | `/jobs/{id}/tasks/{id}/retry` | Requeue a failed card. |
| `GET` | `/images/{id}` · `/thumb` | Card image and thumbnail. |
| `GET` | `/health` · `/ready` · `/stats` | Liveness, readiness, per-tier throughput. |
| `DELETE` | `/jobs/{id}` | Purge a batch immediately. |

An invalid file is reported individually rather than failing the batch — twenty cards and one screenshot yields nineteen leads and one clear message.

```bash
curl -X POST http://localhost:8000/api/v1/jobs \
  -F "files=@card-one.jpg" -F "files=@card-two.jpg"
```

## Testing

```bash
make test           # 173 backend, 7 frontend
make lint           # ruff, pyright, eslint, tsc
make eval           # field accuracy against the known-answer card set
```

Unit tests run on in-memory SQLite and are dependency-free; behaviour that is genuinely PostgreSQL-specific (`SKIP LOCKED` claiming, JSONB) is covered by integration tests. The fixture enables `PRAGMA foreign_keys` explicitly — without it SQLite ignores foreign keys, `ON DELETE CASCADE` does nothing, and a cascade test passes while asserting behaviour PostgreSQL does not share.

CI runs both suites, builds both container images, and applies every migration forward, backward and forward again. An irreversible migration is otherwise discovered during a rollback, which is the worst possible moment.

## Deployment

One EC2 instance running Docker Compose behind Caddy, which terminates TLS and serves the built SPA. Two profiles over one Compose file:

```bash
docker compose -f deploy/docker-compose.prod.yml --profile gpu up -d --build
docker compose -f deploy/docker-compose.prod.yml --profile cpu up -d --build
```

The `cpu` profile is the same stack without the GPU container. The API and worker images are identical between them, so switching is configuration rather than a second deployment. `deploy/ec2/user-data-gpu.sh` handles first boot: it verifies the GPU is visible *from inside a container*, downloads weights with retries, and waits for `/api/v1/ready` rather than reporting success when containers start.

<details>
<summary><strong>About "free tier"</strong></summary>

Worth being straightforward about, because it shapes the whole design: **no AWS free-tier instance can run this.** The free-tier-eligible types have 1–2 GB of RAM and the smallest usable Qwen VLM needs about 3 GB with its vision projector. AWS also has no free GPU hours.

What does exist is the new-account **Free Plan credit pool** — $100 at signup plus up to $100 for onboarding tasks, valid six months. On a `g4dn.xlarge` (T4, ~**$0.54/hour** all-in) that is roughly **7.5 days** of continuous uptime for $100. GPU capacity also needs a Service Quotas increase; new accounts start at zero.

The deployment plan is therefore: run the GPU profile for the evaluation window with billing alarms at $50 and $80, then switch to the CPU profile when ~$40 of credit remains so the URL outlives the GPU budget. The Elastic IP moves with it, so the public URL and its certificate do not change. [docs/RUNBOOK.md](docs/RUNBOOK.md) has the procedure.
</details>

## Project structure

```
backend/          FastAPI application, extraction worker, ORM, tests
  app/api/        HTTP routes
  app/services/   ingestion, normalisation, export, storage, queue
  app/vlm/        provider chain, circuit breakers, prompts, schema
  app/worker/     queue poller
frontend/         React 19 + TypeScript SPA
deploy/           Compose files, Caddyfile, EC2 bootstrap
eval/             Card set, ground truth, accuracy harness
docs/             Architecture, decisions, evaluation, runbook
scripts/          Model download
```

**Stack:** Python 3.12 · FastAPI · SQLAlchemy 2 (async) · Alembic · PostgreSQL 16 · llama.cpp · React 19 · TypeScript · Vite · Tailwind CSS 4 · TanStack Query · Motion · openpyxl · Docker · Caddy

## Known limits

- **Accuracy on real photographs is not yet measured.** The evaluation set is synthetic.
- **One person per card.** A card showing two contacts yields the more prominent one, with the other described in the notes field.
- **Front and back are separate cards.** They are not merged.
- **PDFs are not accepted** — images only, stated in the upload error.
- **Non-Latin scripts are transcribed, not transliterated**, which is correct but untested for accuracy.
- **Single instance.** Storage is a local volume; scaling horizontally needs the S3 backend the storage interface already allows for.

## Privacy and data handling

- Batches, leads and images are deleted after `RETENTION_DAYS` (7 by default), and a user can purge a batch immediately.
- Uploads have **EXIF stripped on ingest**, so stored images carry no GPS trail — a phone photo of a card records where it was taken, and nothing downstream needs that.
- Client IP addresses are stored **only as a salted hash**: enough to rate limit and audit, not enough to make the database personal data on its own.
- With the hosted tier enabled, a card that both self-hosted tiers fail is sent to Alibaba Model Studio in Singapore. Rows processed that way are badged in the UI, and the tier can be switched off entirely.
- Card text is treated as data, never as instruction. Grammar-constrained output means text printed on a card cannot change the response shape.

## Licence

MIT
