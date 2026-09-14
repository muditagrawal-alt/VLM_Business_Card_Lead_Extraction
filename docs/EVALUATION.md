# Evaluation

Every accuracy or latency figure in this project comes from `eval/run_eval.py`
rather than from impressions. This document records what has been measured, on
what, and — just as importantly — what has not been measured yet.

Reproduce any row with:

```bash
cd backend && uv run python ../eval/run_eval.py \
  --tier cpu --base-url http://127.0.0.1:8081/v1 \
  --model Qwen3VL-4B-Instruct-Q4_K_M --max-edge 768
```

## How scoring works

The seven fields the assignment requires are scored per card:
first name, last name, position, company, location, phone, email.

- **Phone** is compared in E.164 form, so printed grouping is irrelevant.
- **Email** and **website** are compared case-insensitively after validation.
- **Names, position, company, location** use a 0.90 similarity threshold, so
  `Meridian Logistics.` matches `Meridian Logistics` but a different company
  does not.
- **A field that should be null and is null counts as correct.** Getting an
  absent field right matters: inventing a job title is a failure this set
  deliberately probes.

## Results

Hardware: Apple M-series, 24 GB, llama.cpp with Metal offload. These are
*local development* numbers; the deployment target is a T4 GPU, which is
expected to be substantially faster and will be measured separately.

| Run | Model | Image | Field accuracy | Perfect cards | p50 | p95 | mean |
|---|---|---|---|---|---|---|---|
| 1 | Qwen3-VL-4B Q4_K_M | 768 px | 82.1 % | 6 / 8 | 24.6 s | 30.9 s | 43.7 s |
| 2 | Qwen3-VL-4B Q4_K_M | 768 px | 82.1 % | 6 / 8 | 18.8 s | 26.4 s | 60.0 s |
| **3** | **Qwen3-VL-4B Q4_K_M** | **768 px** | **100.0 %** | **8 / 8** | **20.6 s** | **30.1 s** | **23.5 s** |

Run 3 per field: all seven at 100 %. All eight cards were answered on the
strictest structured-output rung (`json_schema`); no card needed a weaker
format or a repair attempt.

### What changed between the runs

**Run 1 → 2: raising `max_tokens` from 1536 to 3072.** No effect on accuracy.
The hypothesis — that dense cards ran out of budget — was wrong, and the run
is recorded because the null result is what forced a proper diagnosis.

**Run 2 → 3: making the grammar require every field, and bounding free text.**
Capturing the raw response for a failing card showed the model had read it
perfectly, emitted `raw_text` and `company`, then jumped straight to `notes`
and degenerated into a repetition loop until it hit the token ceiling
(`finish_reason: length`).

Two defects, one visible symptom:

1. Pydantic makes a defaulted field optional, and **an optional property in a
   grammar is one the model may skip.** Every data field is now required in the
   grammar schema — recursively, through the postal address and each phone
   entry — with defaults stripped. Because each field still permits `null`,
   "required" means the model must *answer*; an explicit null is a valid
   answer and never means invent a value.
2. **Free text was unbounded**, which is where a small model degenerates. Every
   string field now carries a `maxLength` matching its database column, capping
   the loop at its source.

The two pathological cards went from 156 s and 184 s to roughly 30 s each,
which is why mean latency fell from 60.0 s to 23.5 s even as accuracy rose.

No frequency or presence penalty was added. A card's domain legitimately
repeats across its email, website and transcription, so penalising repeated
tokens would damage correct output to fix a problem the length cap already
bounds.

## The card set

Eight synthetic cards (`eval/cards/synthetic`), generated with known ground
truth by `eval/generate_cards.py`. The set is built around layouts that break
extraction rather than around easy ones:

| Card | What it probes |
|---|---|
| `01-clean-corporate` | Baseline, left-aligned |
| `02-dark-centred` | Light text on a dark ground, centred |
| `03-no-job-title` | A genuinely absent field must stay null |
| `04-initial-only` | `J.` must not be expanded into a guessed name |
| `05-honorific-suffix` | `Dr.` and `PhD` belong in notes, not the name fields |
| `06-two-people` | Two contacts; the prominent one wins, the other is noted |
| `07-tagline-trap` | A slogan sits where a company name usually does |
| `08-multiple-phones` | Mobile must win over office and fax |

## Honest limits of this number

**100 % here does not mean 100 % in production.** These are clean synthetic
renders: perfect focus, no glare, no perspective skew, no creases, no
low-contrast foil or textured stock. They isolate *reasoning and schema*
failures, which is exactly what they were built to do, and they caught a real
one. They do not test *perception*.

Not yet measured, and required before any accuracy claim is made in the README:

- **Real photographed cards** — phone camera, uneven lighting, angle, glare.
- **A public business-card dataset**, for volume and independence from cards
  chosen by the same person who wrote the prompt.
- **Non-Latin scripts**, where transliteration is the expected failure.
- **Qwen3-VL-8B on a T4**, the actual primary tier.
- **Image resolution.** llama.cpp warns that Qwen-VL wants at least 1024 image
  tokens for reliable grounding, which the current 768 px cap may undercut.
  The trade-off against latency needs measuring, not assuming.
- **The hosted tier**, which answers on a weaker structured-output rung and so
  may behave differently on exactly the cards that needed the grammar.

Runs are kept as JSON under `eval/results/` so a regression can be diffed
card by card rather than argued about.
