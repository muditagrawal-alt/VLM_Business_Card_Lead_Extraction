# Business Card Lead Extraction with a Self-Hosted Qwen VLM

Upload business card images in bulk, extract structured lead data with a
self-hosted **Qwen3-VL** vision-language model, review and correct the results
in the browser, and download them as a formatted Excel workbook.

> **Live URL:** _added on deployment_

## What it extracts

First Name · Last Name · Position / Job Title · Company · Location ·
Phone Number · Email Address

Also captured per card: website, the address broken into parts, secondary
phones and emails, the full text the model read, and a per-field confidence
score.

## Contents

- [How it works](#how-it-works)
- [Design decisions](#design-decisions)
- [Measured results](#measured-results)
- [Running it locally](#running-it-locally)
- [Deployment and cost](#deployment-and-cost)
- [Known limits](#known-limits)

## How it works

```
upload → validate & deduplicate → EXIF orient, strip metadata, downscale
       → queue (PostgreSQL) → worker → VLM → normalise → lead → Excel
```

A card is never sent to a single point of failure. Each one runs through an
ordered chain of inference tiers, each behind its own circuit breaker:

| Tier | Model | Where | Latency per card |
|---|---|---|---|
| 1 | Qwen3-VL-8B-Instruct (Q8_0) | Self-hosted, NVIDIA T4 | ~2–4 s |
| 2 | Qwen3-VL-4B-Instruct (Q4_K_M) | Self-hosted, CPU | ~20–30 s |
| 3 | Qwen3-VL (hosted Qwen API) | Alibaba Model Studio | ~3–6 s |

If a tier fails or times out, the next takes over. After three consecutive
failures its breaker opens and it is skipped entirely until a probe succeeds —
without that, a dead GPU container would cost every card in a 50-card batch a
full timeout before falling through. **Every lead records which tier produced
it**, shown as a badge in the UI and summarised in the workbook, so a batch
that spanned tiers is never presented as though it did not.

### Structured output

On the self-hosted tiers the JSON schema is compiled into a grammar, so the
model **cannot** emit malformed JSON or unknown fields. Hosted providers vary,
so the client degrades through `json_schema` → `json_object` → mining the first
JSON object out of the text, with one repair attempt that feeds the validation
error back. The rung that succeeded is stored per card, which keeps accuracy
comparisons between tiers honest.

Every field is **required but nullable**. The model must answer for each one,
and an explicit `null` is a valid answer — it never means invent a value. A
card printing no job title yields a null title, flagged for review rather than
filled with something plausible.

### After the model

Raw model output is not a usable lead, so a normalisation layer runs over it:
phone numbers to E.164, emails validated, honorifics and suffixes stripped from
names, and one primary phone chosen from however many the card lists (mobile
beats office beats fax). Confidence is a grounding check rather than a model
probability: it asks whether a value survived validation and appears in the
text the model transcribed, which is what catches an invented field.

## Design decisions

The full reasoning is in [docs/DECISIONS.md](docs/DECISIONS.md). The ones worth
stating up front:

**No separate OCR engine.** Qwen3-VL reads card text directly and more
accurately than a CRAFT/CRNN pipeline; feeding EasyOCR output into it would add
a worse signal to a better reader. Docling is a PDF/DOCX layout converter that
delegates to an OCR engine for images, so "Docling + EasyOCR" is EasyOCR plus a
large dependency tree — and EasyOCR pulls in PyTorch, roughly 800 MB of RAM
that is better spent on a larger model. A lightweight ONNX cross-check remains
an option if evaluation ever shows a gap.

**PostgreSQL for both the leads and the queue.** It is already a dependency, so
`SELECT … FOR UPDATE SKIP LOCKED` gives a durable queue several workers can
share without adding Redis and a broker to operate. Crash safety comes from
leases: a worker that dies mid-card leaves a lease that lapses, so a crash
costs one retry rather than a lost card.

**A worker process separate from the API.** A card on the CPU tier can take
half a minute; sharing a process would make the web tier unresponsive during a
batch.

## Measured results

Every figure here comes from `eval/run_eval.py`, not from impressions.

| Model | Image | Field accuracy | Perfect cards | p50 | p95 |
|---|---|---|---|---|---|
| Qwen3-VL-4B Q4_K_M | 768 px | **100.0 %** | 8 / 8 | 20.6 s | 30.1 s |

Measured on Apple Silicon with Metal offload, against eight synthetic cards
built around the layouts that break extraction: a dark centred card, one with
no job title, a first name given only as an initial, an honorific and suffix, two
people on one card, a slogan where a company name usually sits, and a card
listing mobile, office and fax.

**This is not a production accuracy claim.** Those cards are clean renders with
perfect focus and no glare, skew or creases. They isolate reasoning and schema
failures — which is what they were built for, and they caught a real one — but
they do not test perception. Real photographed cards, a public dataset, and the
8B model on a T4 are all still to be measured; see
[docs/EVALUATION.md](docs/EVALUATION.md) for the full list and for the defects
the harness exposed along the way.

## Running it locally

Requires Docker, Python 3.12, Node 22, `uv`, and llama.cpp.

```bash
make install        # backend and frontend dependencies
make models         # Qwen3-VL weights (~11 GB, resumable)
make db-up          # PostgreSQL on port 5433
make migrate

make llama-cpu      # terminal 2: serve the 4B model
make dev            # terminal 3: API, worker and frontend
```

Then open <http://localhost:5173>. The API documents itself at
<http://localhost:8000/api/docs>.

The dev database uses port **5433** deliberately: a locally installed
PostgreSQL usually holds 5432 and, being bound to `127.0.0.1`, silently wins
the connection over Docker's wildcard bind.

```bash
make test           # 172 backend tests, 7 frontend
make lint           # ruff, pyright, eslint, tsc
make eval           # accuracy against the card set
```

## Deployment and cost

One EC2 instance running Docker Compose behind Caddy, which terminates TLS and
serves the built SPA. `deploy/ec2/user-data-gpu.sh` handles first boot: it
verifies the GPU is visible from inside a container, downloads the weights with
retries, and waits for `/api/v1/ready` rather than reporting success when
containers start.

Two profiles, one compose file:

```bash
docker compose -f deploy/docker-compose.prod.yml --profile gpu up -d --build
docker compose -f deploy/docker-compose.prod.yml --profile cpu up -d --build
```

The `cpu` profile is the same stack without the GPU container. The API and
worker images are identical between them, so switching is configuration rather
than a different deployment.

### About "free tier"

Worth being straightforward about, because it shapes the whole design: **no
AWS free-tier instance can run this.** The free-tier-eligible types have 1–2 GB
of RAM and the smallest usable Qwen VLM needs about 3 GB with its vision
projector. AWS also has no free GPU hours.

What does exist is the new-account **Free Plan credit pool** — $100 at signup
plus up to $100 for onboarding tasks, valid six months. On a g4dn.xlarge (T4,
about **$0.54/hour** all-in) that is roughly **7.5 days** of continuous uptime
for $100, or 15 for $200. GPU capacity also needs a Service Quotas increase;
new accounts start at zero.

So the deployment plan is: run the GPU profile for the evaluation window, with
billing alarms at $50 and $80, and switch to the CPU profile when about $40 of
credit remains so the URL outlives the GPU budget. The Elastic IP moves with
it, so the public URL and its certificate do not change.
[docs/RUNBOOK.md](docs/RUNBOOK.md) has the procedure.

## Known limits

- **Accuracy on real photographs is not yet measured.** See above.
- **One person per card.** A card showing two contacts yields the more
  prominent one, with the other described in the notes field.
- **Front and back are separate cards.** They are not merged.
- **PDFs are not accepted** — images only, stated in the upload error.
- **Non-Latin scripts are transcribed, not transliterated**, which is correct
  but untested for accuracy.
- **Single instance.** Storage is a local volume, so scaling horizontally would
  need the S3 backend the storage interface already allows for.

## Privacy

Batches, leads and images are deleted after seven days, and a user can purge a
batch immediately. Uploads have EXIF stripped on ingest, so stored images carry
no GPS trail. Client IP addresses are stored only as a salted hash. If the
hosted tier is enabled, a card that both self-hosted tiers fail is sent to
Alibaba Model Studio in Singapore; rows processed that way say so in the UI, and
the tier can be switched off entirely.

## Repository layout

```
backend/     FastAPI app, worker, PostgreSQL models, tests
frontend/    React + TypeScript SPA
deploy/      Compose files, Caddyfile, EC2 bootstrap
eval/        Card set, ground truth, accuracy harness
docs/        Plan, decisions, architecture, evaluation, runbook
scripts/     Model download
```

## Licence

MIT
