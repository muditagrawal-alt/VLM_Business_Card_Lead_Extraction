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

Runs 1–4 are local development numbers: Apple M-series, 24 GB, llama.cpp
with Metal offload. Runs 5 and 6 are the deployed service — a `Standard_NC4as_T4_v3`
on Azure (one T4, 16 GB) — driven through the public URL, so they include upload,
queueing and normalisation, not just inference.

| Run | Model | Image | Field accuracy | Perfect cards | p50 | p95 | mean |
|---|---|---|---|---|---|---|---|
| 1 | Qwen3-VL-4B Q4_K_M | 768 px | 82.1 % | 6 / 8 | 24.6 s | 30.9 s | 43.7 s |
| 2 | Qwen3-VL-4B Q4_K_M | 768 px | 82.1 % | 6 / 8 | 18.8 s | 26.4 s | 60.0 s |
| **3** | **Qwen3-VL-4B Q4_K_M** | **768 px** | **100.0 %** | **8 / 8** | **20.6 s** | **30.1 s** | **23.5 s** |
| **4** | **Qwen3-VL-8B Q8_0** | **768 px** | **100.0 %** | **8 / 8** | **64.4 s** | **72.8 s** | **65.8 s** |
| **5** | **Qwen3-VL-8B Q8_0 · live T4, cold** | **768 px** | **100.0 %** | **8 / 8** | **10.5 s** | **34.2 s** | **16.3 s** |
| **6** | **Qwen3-VL-8B Q8_0 · live T4, warm** | **768 px** | **100.0 %** | **8 / 8** | **8.9 s** | **12.1 s** | **9.5 s** |

Runs 3 to 6 scored 100 % on every field, and every card was answered on the
strictest structured-output rung (`json_schema`) — no card needed a weaker
format or a repair attempt. Run 5 was the first batch after the GPU server
started: the two cards that landed in its cold slots took 34 s each while CUDA
kernels compiled and the projector ran for the first time, and everything
after them took 9–13 s. Run 6, on the warm server, cleared the batch in 41 s
of wall-clock time with two cards in flight.

### This card set is now saturated

Both models score 100 %, so **the synthetic set can no longer tell them apart.**
It was built to catch reasoning and schema failures, it caught a significant
one (see below), and it has now done its job. Choosing between the 4B and the
8B on this evidence is not possible, and any claim that the 8B is "more
accurate here" would be unsupported.

What the numbers *do* support:

- **On CPU, the 4B is the right choice.** Equal accuracy at roughly a third of
  the latency (23.5 s against 65.8 s) settles the fallback tier.
- **The 8B's value has to be proven on harder input.** It is the primary tier
  because published document benchmarks favour it and because the T4 makes
  its latency a non-issue (run 6: 8.9 s median) — not because this card set
  showed an advantage.

Runs 1–4 are Metal on a laptop and are not deployment numbers; runs 5 and 6
are. The T4 is roughly seven times faster than Metal for the 8B (8.9 s against
64.4 s at p50), and about twelve times faster than the 2-vCPU AWS instance
running the 4B (113 s median), which is the only configuration on which a card
has hit the 600 s ceiling.

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

- **Cards hard enough to separate the models.** This is now the top priority:
  with the set saturated, the next run needs input that actually discriminates.

- **Real photographed cards** — phone camera, uneven lighting, angle, glare.
- **A public business-card dataset**, for volume and independence from cards
  chosen by the same person who wrote the prompt.
- **Non-Latin scripts**, where transliteration is the expected failure.
- **Image resolution.** llama.cpp warns that Qwen-VL wants at least 1024 image
  tokens for reliable grounding, which the current 768 px cap may undercut.
  The trade-off against latency needs measuring, not assuming.
- **The hosted tier**, which answers on a weaker structured-output rung and so
  may behave differently on exactly the cards that needed the grammar.

Runs are kept as JSON under `eval/results/` so a regression can be diffed
card by card rather than argued about.
