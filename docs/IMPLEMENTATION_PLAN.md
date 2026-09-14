# VLM Business Card Lead Extraction — Implementation Plan

> **Status:** Agreed 14 Sep 2026 — this is the plan for the final product. Each phase is checked off as it ships; changes to decisions are recorded in `docs/DECISIONS.md`.

---

## 0. Decisions at a glance

| Area | Decision | One-line why |
|---|---|---|
| **Primary VLM** | **Qwen3-VL-8B-Instruct**, GGUF **Q8_0**, llama.cpp `llama-server` CUDA build on **g4dn.xlarge (T4 16 GB)** | The 4B→8B gap is largest on document/OCR workloads; Q8 is near-lossless and fits the T4 with room for 2 parallel slots; ~2–4 s/card |
| **Fallback 1** | **Qwen3-VL-4B-Instruct**, GGUF **Q4_K_M**, llama-server **CPU build** — runs (a) as a second container **on the same GPU box** for instant failover and (b) as the `cpu` Compose profile on an 8 GB instance for cold-standby / cost-down mode | Same image, same API, zero extra cost when co-located; keeps the URL alive after credits run low |
| **Fallback 2** | **Alibaba Model Studio (official Qwen API)** — `qwen3-vl-plus` via its OpenAI-compatible endpoint, **1M free tokens / 90 days** for new users. OpenRouter paid Qwen-VL is a config-only alternate | `qwen/qwen-2.5-vl-7b-instruct:free` **no longer exists** on OpenRouter (404, retired) and OpenRouter currently has no free Qwen vision model at all. Model Studio is still Qwen, still free, and officially hosted |
| Failover | Ordered provider chain with per-provider circuit breaker + health probes; provider recorded per task; UI badge when a card was processed off-box | Reviewer sees resilience, not a spinner |
| OCR | None in the primary path. Optional RapidOCR cross-check only if eval numbers demand it | Qwen-VL *is* the OCR; Docling/EasyOCR add RAM and a worse signal |
| Database | PostgreSQL 16 container, SQLAlchemy 2.0 async, Alembic; SQLite for unit tests | Structured leads + JSONB raw output + durable `SKIP LOCKED` job queue |
| Queue | Postgres task table + dedicated worker container | Durable, restart-safe, no Redis/Celery |
| Backend | Python 3.12, FastAPI, Pydantic v2, `uv` | Typed, async, OpenAPI for free |
| Frontend | Vite + React + TS, Tailwind, shadcn/ui, TanStack Query/Table; static build served by Caddy | No Node in prod |
| Export | `openpyxl` .xlsx (styled) + .csv | Required deliverable, polished |
| Hosting | 1× EC2 g4dn.xlarge (`gpu` profile) — or 1× 8 GB instance (`cpu` profile) — Docker Compose, Caddy auto-HTTPS, Elastic IP + `sslip.io` or your domain | One box, two profiles, same repo |
| CI/CD | GitHub Actions: ruff + pyright + pytest + frontend build + docker build → SSH deploy on `main` | The repo is judged too |

### Note on the CPU tier
The CPU box is **not free**. Legacy free-tier instances (1–2 GB RAM) cannot load any Qwen VLM; an 8 GB instance costs ~$50–70/month **from the same credit pool** as the GPU. So the CPU fallback is designed as (a) a **co-located container on the GPU box** — genuinely $0 extra — and (b) a **cold-standby deployment profile**, not a second always-on server. Running GPU + a separate CPU instance simultaneously would cut credit lifetime from ~15 days to ~13 for no resilience gain (if the box dies, the app dies with it regardless).

---

## 1. Requirements traceability

| # | Assignment requirement | How we satisfy it | Where |
|---|---|---|---|
| 1 | Deploy a Qwen VLM on free-tier/equivalent AWS | Qwen3-VL-8B on g4dn.xlarge paid for by AWS Free Plan credits; Qwen3-VL-4B `cpu` profile as the free-tier-shaped alternative; both self-hosted with llama.cpp | `deploy/`, §3, §9 |
| 2 | Bulk upload | Drag-and-drop multi-file (≤ 50/batch, ≤ 10 MB each, JPEG/PNG/WebP/HEIC), client-side downscale, one job per batch | `frontend/`, `POST /api/v1/jobs` |
| 3 | Extract 7 fields | JSON-schema-constrained VLM output + normalisation/validation layer | `backend/app/vlm`, `services/extraction` |
| 4 | Display leads | Live table with thumbnails, per-field confidence, provider badge, inline edit | `frontend/src/pages/JobPage` |
| 5 | Excel download | `GET /api/v1/jobs/{id}/export.xlsx` (+ `.csv`) | `services/export` |
| 6 | Public URL | `https://<eip>.sslip.io` (or custom domain), Let's Encrypt via Caddy | `deploy/Caddyfile` |

---

## 2. Inference tiers

### 2.1 The chain

```
Task ──▶ [1] llama-gpu  Qwen3-VL-8B Q8_0   (same box, T4)        timeout 60 s
           │ breaker open / timeout / 5xx
           ▼
         [2] llama-cpu  Qwen3-VL-4B Q4_K_M (same box, CPU)       timeout 180 s
           │ breaker open / timeout / 5xx
           ▼
         [3] Model Studio qwen3-vl-plus    (Alibaba, Singapore)  timeout 90 s
           │ all failed
           ▼
         task.status = failed (error kept), retried once after backoff
```

- **Circuit breaker per provider:** opens after 3 consecutive failures/timeouts, half-open probe every 60 s, closes on success. Health probes (`/health` on both llama-servers) run every 15 s independently so a crashed GPU server is skipped *before* a task hits it.
- **Provider + model + latency recorded on every task** → shown in the UI (small badge: "GPU · 8B", "CPU · 4B", "Cloud · qwen3-vl-plus") and in the Excel Summary sheet.
- **Off-box disclosure:** tier 3 sends the card image to Alibaba Cloud (Singapore). `EXTERNAL_FALLBACK_ENABLED=true` by default for the demo, documented in README; rows processed externally carry a badge. Flip to `false` and the chain stops at tier 2.
- **Worker concurrency** follows the active tier: 2 when GPU healthy (`--parallel 2` on llama-gpu), 1 when on CPU, 2 on cloud (rate-limit aware).

### 2.2 Structured output — degradation ladder

llama-server converts `response_format: {type: "json_schema"}` into a GBNF grammar, so tiers 1–2 **cannot** emit malformed JSON or unknown keys. Hosted APIs vary, so the client degrades:

1. `json_schema` (strict) → 2. `json_object` + schema in prompt → 3. plain prompt + first-`{…}`-block extraction → Pydantic validation → one "repair" retry with the validation error fed back. Which rung succeeded is logged. Same Pydantic model is the single source of truth for the JSON schema, the DB row, the API response and the Excel columns.

### 2.3 Why these models (verified Sept 2026)

| Tier | Model | Memory | Latency / card* | Notes |
|---|---|---|---|---|
| 1 GPU | **Qwen3-VL-8B-Instruct Q8_0** | ~8.7 GB weights + ~1.2 GB mmproj + ~1.2 GB KV (2 × 4096) + ~1.5 GB compute ≈ **12.5–13 GB of 15 GB** VRAM | ~2–4 s | Q6_K (~6.6 GB) is the drop-in if VRAM is tight. T4 is Turing (sm_75): fp16 fine, no bf16 — irrelevant for GGUF |
| 2 CPU | **Qwen3-VL-4B-Instruct Q4_K_M** | ~4.5 GB host RAM (mlock) | ~30–60 s on the g4dn's 4 vCPU; ~40–80 s on a 2-vCPU box | DocVQA ≈ 94 / OCRBench ≈ 86 — the smallest size with solid document accuracy. Qwen3.5-4B is the A/B challenger in the spike (parity on OCR, newer gen) |
| 3 Cloud | **qwen3-vl-plus** (Model Studio) | — | ~3–6 s | 1M free input + 1M output tokens per model, 90 days, international (Singapore) scope. A card ≈ 0.7–1k input tokens → **> 1,000 cards free**. Alternate: OpenRouter paid Qwen-VL (e.g. `qwen/qwen3.8-flash`, ≈ $0.0002/card) — same client, different env |

\* Image capped at 768 px long edge. Measured in Phase 0, not assumed.

Not chosen: Qwen3.6/3.7/3.8 open weights (27B+; too big for T4 at useful quant), Ollama (no mmproj for newer Qwen families), vLLM (heavier, T4 lacks bf16/FP8 — llama.cpp is the simpler win at this scale), Qwen2.5-VL (superseded).

### 2.4 Host RAM budget on g4dn.xlarge (16 GB)

| Component | RAM |
|---|---|
| llama-gpu (weights offloaded; host side buffers + page cache) | ~1.5 GB |
| llama-cpu (4B Q4 + mmproj, mlock) | ~4.5 GB |
| postgres / api / worker / caddy | ~0.8 GB |
| OS + Docker | ~0.7 GB |
| **Total** | **~7.5 GB** — comfortable; 4 GB swap as insurance |

---

## 3. Extraction pipeline (per image)

```
upload → validate (magic bytes + Pillow decode, size caps, HEIC via pillow-heif) → sha256 dedupe
→ EXIF auto-orient → strip metadata → resize ≤ 768 px long edge, JPEG q85 → store → enqueue task
→ worker: provider chain (§2.1) → schema-constrained JSON → parse/validate
→ normalise: phone → E.164 (`phonenumbers`, region hint from address/country), email lowercase + `email-validator`,
  name casing, whitespace, honorific/suffix stripping (`nameparser` fallback only)
→ derive Location = "City, Region, Country" from address parts
→ confidence per field (present? validator pass? model self-report? [optional OCR agreement])
→ persist lead + raw JSON + provider + timings → mark task done
```

**Schema the VLM is constrained to:**
```json
{
  "first_name": "string|null", "last_name": "string|null", "full_name_as_printed": "string|null",
  "position": "string|null", "company": "string|null",
  "emails": ["string"],
  "phones": [{"number": "string", "type": "mobile|office|fax|other|unknown"}],
  "website": "string|null",
  "address": {"street": "string|null", "city": "string|null", "state": "string|null", "country": "string|null", "postal_code": "string|null"},
  "raw_text": "string", "notes": "string|null"
}
```
Prompt rules: *transcribe only what is printed; never invent; `null` when absent; keep original script; phones exactly as printed (we normalise); if two people appear, extract the most prominent and note the other.* Primary phone = mobile > office > other; primary email = first valid. Extras go to additional Excel columns and the detail drawer.

---

## 4. Architecture

```mermaid
flowchart LR
  U[Browser<br/>React SPA] -->|HTTPS| C[Caddy<br/>TLS · static · proxy]
  C -->|/api| A[FastAPI api]
  C -->|/| S[(static build)]
  A --> P[(PostgreSQL 16)]
  A --> F[(volume: images · thumbs)]
  W[worker] --> P
  W --> F
  W -->|tier 1| G[llama-gpu<br/>Qwen3-VL-8B Q8_0<br/>T4 · CUDA]
  W -.->|tier 2| Cp[llama-cpu<br/>Qwen3-VL-4B Q4_K_M]
  W -.->|tier 3| M[Model Studio<br/>qwen3-vl-plus]
  B[backup cron] --> P
  B --> S3[(S3 nightly dump)]
  subgraph EC2 g4dn.xlarge  ·  profile: gpu
    C; A; S; P; F; W; G; Cp; B
  end
```

`cpu` profile = identical minus `llama-gpu`, on any 8 GB instance. Only Caddy exposes 80/443; llama-servers are unreachable from the internet.

**Data model** (unchanged from v1): `jobs`, `tasks` (+ `provider`, `model`, `latency_ms`, `output_mode`), `images` (sha256-deduped), `leads` (7 required fields + extras + `confidence` JSONB + `edited_by_user`). Retention sweep at 7 days.

**API** (`/api/v1`): `POST /jobs` · `GET /jobs/{id}` · `GET /jobs/{id}/leads` · `PATCH /leads/{id}` · `GET /jobs/{id}/export.xlsx|csv` · `GET /images/{id}/thumb` · `DELETE /jobs/{id}` · `GET /health` · `GET /ready` (DB + at least one healthy provider) · `GET /stats` (per-provider counts/latency — feeds the README numbers). Rate limits via `slowapi`; optional `APP_ACCESS_CODE`.

**Frontend**: Upload (dropzone, previews, client downscale) → Job (progress + honest ETA from rolling per-tier latency, table with confidence chips + provider badge + duplicate badge, row drawer with full image + all fields + inline edit, failed-row retry, **Download Excel**) → History (localStorage). Mobile-usable. Designed empty/loading/error states.

**Excel**: sheet *Leads* (`# · First Name · Last Name · Position · Company · Location · Phone · Email · Website · Address · Other Phones · Other Emails · Confidence · Provider · Source File · Extracted At`) — bold header, frozen row, autofilter, widths, `mailto:` links, phone as text, amber low-confidence cells; sheet *Summary* (job, date, per-tier counts, latencies, duplicates). CSV with UTF-8 BOM.

---

## 5. Repository layout

```
.
├── README.md · docs/{IMPLEMENTATION_PLAN, ARCHITECTURE, DECISIONS, RUNBOOK, EVALUATION}.md
├── backend/
│   ├── pyproject.toml (uv) · Dockerfile (multi-stage, non-root, healthcheck) · alembic/ · tests/{unit,integration,fixtures}
│   └── app/  main.py · config.py · api/ · core/ · models/ · schemas/ · services/{images,extraction,export,storage,retention}
│             · vlm/{provider.py, openai_compat.py, chain.py, circuit_breaker.py, prompts.py} · worker/runner.py
├── frontend/  Vite + React + TS · Dockerfile (build stage only → static into Caddy image)
├── deploy/
│   ├── docker-compose.yml            # dev
│   ├── docker-compose.prod.yml       # profiles: gpu | cpu   (caddy, api, worker, postgres, backup, llama-cpu, [llama-gpu])
│   ├── Caddyfile
│   ├── llama/  gpu.env · cpu.env · download-models.sh (8B Q8_0 + mmproj, 4B Q4_K_M + mmproj, sha256-verified)
│   └── ec2/    user-data-gpu.sh · user-data-cpu.sh · terraform/ (optional: EC2 + SG + EIP + IAM + billing alarms)
├── eval/  cards/ · ground_truth.json · run_eval.py (per-provider accuracy + latency report)
├── .github/workflows/  ci.yml · deploy.yml
├── Makefile · .env.example
```

---

## 6. Deployment (AWS)

### 6.1 Day-0 actions (do these today — they gate the GPU path)
1. **Confirm account type**: Free Plan (auto-closes at $0, resources shut down) vs Paid Plan (bills on-demand after credits). Note which.
2. **Complete the 5 onboarding tasks** → unlocks the second $100 (~30 min).
3. **Service Quotas → EC2 → "Running On-Demand G and VT instances" → request 4 vCPUs** in **us-east-1**. New accounts start at 0; approval takes hours–days and is sometimes refused. *This is the only item on the GPU critical path.*
4. **Billing alarms** at $50 / $80 spent now (CloudWatch → email); add $120 / $160 when the second $100 is unlocked.
5. **Alibaba Cloud Model Studio** — sign up, **International (Singapore)** scope, activate, create API key, confirm `qwen3-vl-plus` free quota shows 1M tokens.
6. Create the GitHub repo (private until submission).

### 6.2 Instance & image
- **g4dn.xlarge** — 4 vCPU, 16 GB RAM, 1× T4 16 GB, **us-east-1**.
- AMI: **AWS Deep Learning Base OSS Nvidia Driver GPU AMI (Ubuntu 24.04)** — NVIDIA driver, Docker and `nvidia-container-toolkit` preinstalled and version-matched. Avoids the single most common GPU-deploy failure (driver/toolkit mismatch).
- Root **60 GB gp3** (~$5/mo): 8B Q8 (8.7 GB) + 4B Q4 (2.5 GB) + mmprojs (2 GB) + CUDA image (~3 GB) + Postgres + images.
- Elastic IP → `https://<ip>.sslip.io` (zero DNS) or your domain (one A record).
- llama-gpu: `ghcr.io/ggml-org/llama.cpp:server-cuda` — `-m qwen3-vl-8b-q8_0.gguf --mmproj mmproj-f16.gguf -ngl 99 -fa on -c 8192 --parallel 2 --jinja --host 0.0.0.0` (8192 total ctx = 4096 per slot; a card ≈ 1.5k tokens).
- llama-cpu: `ghcr.io/ggml-org/llama.cpp:server` — `-m qwen3-vl-4b-q4_k_m.gguf --mmproj … -c 4096 --parallel 1 -t 4 --mlock --jinja`.
- Compose `deploy.resources.reservations.devices` for the GPU; memory limits per container; `restart: unless-stopped`; healthchecks; 4 GB swap.

### 6.3 Credit budget (all-in ≈ $0.54/hr on-demand incl. EBS + IPv4)

| Scenario | Credits | Uptime |
|---|---|---|
| GPU 24/7 | $100 | ~7.7 days |
| GPU 24/7 | $200 | **~15.5 days** |
| GPU 16 h/day (EventBridge stop/start, hours stated in README) | $200 | ~23 days |
| Dev phase (start box only when needed, ~4 h/day × 7 days) | ~$15 | — |
| `cpu` profile, m7i-flex.large / t4g.large | ~$2.6/day | $40 ≈ 15 days |

**Runbook rule:** develop locally (Mac/Metal) so AWS spend is ~$0 until deployment; run GPU 24/7 from submission through the review window; **when ~$40 of credit remains, switch to the `cpu` profile** (`make switch-cpu`: stop g4dn → launch 8 GB instance from `user-data-cpu.sh` → re-associate EIP → restore last DB dump → ~15 min) so the URL outlives the credits. If you're on the Free Plan and want the URL alive past credits, upgrade to Paid before $0 — your call, documented.

**Spot (optional):** g4dn.xlarge spot ≈ $0.16–0.21/hr → ~1,000 hrs on $200. Persistent spot request + `stop` interruption behaviour + EIP auto-resumes when capacity returns, but downtime during an interruption is unbounded. Fine for dev; **on-demand for the review window.**

### 6.4 CD
`deploy.yml` on push to `main`: SSH (deploy key) → `git pull` → `docker compose --profile $PROFILE up -d --build` → wait `/ready` → smoke (upload fixture card → expect 1 lead). Rollback = checkout previous tag + `up -d`. Caddy data volume persisted (Let's Encrypt rate limits); staging CA while iterating.

---

## 7. Production-grade checklist

**Security** — HTTPS only + HSTS · magic-byte + decode upload validation · pixel-bomb guard · EXIF stripped · size/count caps · rate limits + optional access code · CORS same-origin · non-root containers, no public ports except Caddy · SG: 22 to your IP only · secrets via `.env` (600) / Actions secrets · pinned lockfiles + Dependabot · card text treated as data (grammar-constrained output neuters prompt injection on tiers 1–2; tier 3 gets the same system prompt + validation).

**Reliability** — health/ready + compose healthchecks + auto-restart · per-tier timeouts, breakers, lease-based crash recovery, max 2 attempts · graceful worker shutdown · swap + memory limits · nightly `pg_dump` → S3 (14-day retention), restore tested · **chaos test in Phase 5: `docker kill llama-gpu` mid-batch → batch still completes via tiers 2/3, GPU auto-restarts, breaker closes** · billing + status-check alarms.

**Observability** — structlog JSON with request/job/task ids · per-task timings + provider persisted → `/stats` · Docker log rotation · optional `/metrics`.

**Privacy** — 7-day retention, user delete, no images in logs, README states what is stored, where (region), and that tier 3 leaves the box.

---

## 8. Testing & evaluation

| Layer | What | Tooling |
|---|---|---|
| Unit | normalisation (phone/email/name/location), confidence rules, Excel builder, schema ↔ JSON-schema, breaker state machine, degradation ladder | pytest, SQLite |
| Integration | API + worker lifecycle with a **mock provider chain** (fixture JSON, injected failures/timeouts → verify fallback order + recorded provider); upload edge cases (HEIC, EXIF-rotated, 0-byte, PNG-as-.jpg, 51 files, 11 MB) | pytest + httpx, Postgres via testcontainers |
| Contract | each real provider honours (or gracefully degrades from) the JSON schema | `@pytest.mark.model`, run locally/nightly |
| Frontend | table/edit/upload components; one Playwright smoke (3 cards → 3 rows → xlsx downloads) | Vitest, Playwright |
| **Accuracy eval** | 30–50 cards (clean, dark, vertical, multilingual, glare, two-sided). Per-field exact match after normalisation (names fuzzy ≥ 0.9). **Reported per tier** so the README can say "GPU 8B: X %, CPU 4B: Y %, cloud: Z %, p50/p95 latency each" | `eval/run_eval.py` → `docs/EVALUATION.md` |

Targets: ≥ 92 % field accuracy on clean printed cards (8B), ≥ 95 % on email/phone, 0 malformed outputs, batch of 50 completes under load, chaos test passes.

---

## 9. Phased plan (compressed to ~4 working days)

| Day | Scope | Exit criteria |
|---|---|---|
| **Day 0 (today, ~1 h)** | AWS CLI profile on your machine; **file G-instance quota (4 vCPUs) in us-east-1**; billing alarms $50/$80; Model Studio (Singapore) key; GitHub repo; repo scaffold + `.env.example` + Makefile | Quota request submitted; `aws sts get-caller-identity` works; Model Studio key returns a completion |
| **Day 1** | **Local spike on Mac/Metal**: 8B Q8_0 vs Q6_K, 4B Q4_K_M, `qwen3-vl-plus` on 10 cards → quant + image size fixed. **Backend core**: config, DB + Alembic, upload/preprocess, task queue + worker, provider chain + breakers + degradation ladder, prompt + schema, normalisation, all endpoints, unit + integration tests, CI green | `curl` a batch → leads in DB via real local llama-server; injected-failure tests prove fallback order |
| **Day 2** | **Frontend** (upload, job page, table, drawer, edit, badges, history, responsive) + **Excel/CSV export** + Summary sheet | Full flow end-to-end locally; xlsx opens cleanly |
| **Day 3** | **Deploy**: g4dn.xlarge from DL Base AMI (or `cpu` profile if quota still pending), compose `gpu` profile, Caddy/TLS on sslip.io, model download, backups, alarms, CD workflow. **Eval**: dataset + real cards, per-tier accuracy report, prompt/image-size tuning, chaos test, 50-card load test | **Public HTTPS URL works**; EVALUATION.md has numbers; chaos test passes |
| **Day 4** | README (architecture, tiers, decisions, cost, limits, demo GIF), RUNBOOK (credit switch, restore), `/code-review` + security pass, a11y pass, buffer for whatever slipped | Stranger can deploy from README in < 30 min; submission-ready |

Phases on Days 1–2 don't touch AWS at all. If the quota request is still pending on Day 3, deploy the `cpu` profile first (same repo, `COMPOSE_PROFILES=cpu`), then flip to GPU when approved — a 15-minute switch, no code change.

## 10. Risks

| Risk | Mitigation |
|---|---|
| **G-instance quota refused/slow** | Filed day 0; `cpu` profile is a complete deployment; GPU tier is config, not code |
| **Credits exhausted mid-review** | Alarms at $80/120/160; box stopped during dev; `make switch-cpu` at $160; hours stated in README if scheduled |
| Free Plan auto-close at $0 | Runbook says upgrade to Paid before $0 if the URL must outlive credits |
| T4 VRAM OOM on Q8 + 2 slots | Q6_K drop-in; `--parallel 1`; measured in spike |
| CUDA driver / toolkit mismatch | DL Base AMI (pre-matched); llama.cpp image tag pinned |
| Model Studio quota/rate limits or model rename | Model id + base URL in env; startup probe lists models; OpenRouter paid Qwen-VL as alternate config; breaker handles 429s |
| Hallucinated fields | "never invent" rules, nullable schema, validators, confidence chips, inline edit |
| Weird uploads (HEIC, 20 MP, rotated, PDF) | pillow-heif, auto-orient, decode-based validation, friendly 413/415; PDF documented unsupported |
| Public URL abused | Rate limits, queue cap with "busy" message, optional access code |
| Let's Encrypt rate limit | Persist Caddy volume; staging CA while iterating |
| Multi-card / two-sided photos | Out of scope v1 (documented); `notes` captures second person |

---

## 11. Locked context (from review, 14 Sep 2026)

| Item | Decision |
|---|---|
| AWS credits | **$100 available now**; second $100 unlocked on demand. Plan type (Free vs Paid) still to confirm — matters only for the auto-close-at-$0 behaviour |
| Region | **us-east-1** (moved from ap-south-1). File the G-instance quota request **in us-east-1** |
| Domain | None → `https://<elastic-ip>.sslip.io` |
| Timeline | ~1 week shared with a second assignment → **this one gets ~3.5–4 days**; schedule in §9 is compressed accordingly. Optional items (Terraform, Playwright, `/metrics`, OCR cross-check, spot) are cut unless time is left over |
| Eval set | Public business-card dataset (selected in Phase 5) **+ your real cards**. Real cards stay local and `.gitignore`d (PII); only synthetic/public samples are committed |
| Tier 3 (Model Studio) | **On by default** with the off-box disclosure badge |
| Local dev | Your Mac (arm64, 24 GB) runs both GGUFs on Metal via llama.cpp → spike + all development happen locally at **$0 AWS spend**; the GPU box is started only for deployment and the review window |

**Budget consequence:** with dev at $0, the $100 covers ~7.7 days of 24/7 GPU. Unlock the second $100 (5 onboarding tasks) **before submission**, not after — it's the difference between 7 and 15 days of uptime.

## 12. Out of scope
Multi-card detection / front-back merging · CRM push / vCard · user accounts · multi-worker GPU autoscaling · active learning from user edits.
