# Decisions

Why this system is built the way it is. Each entry records the alternatives and
the reason one was chosen, so a reviewer can disagree with the reasoning rather
than guess at it.

---

## 1. Qwen3-VL, self-hosted with llama.cpp

**Chosen:** Qwen3-VL-8B-Instruct at Q8_0 as the primary tier, Qwen3-VL-4B at
Q4_K_M as the self-hosted fallback, served by llama.cpp's `llama-server`.

**Why 8B and 4B, not 2B.** The gap between sizes is largest on exactly the
document and OCR benchmarks that matter here (DocVQA, OCRBench), and smallest
on general reasoning, which this task does not need. 2B remains a documented
option if latency on a constrained instance forces it.

**Why Qwen3-VL and not Qwen3.5.** Qwen3.5's small tier is newer and natively
multimodal, but on document tasks it measures at parity or slightly behind
(OCRBench ≈ 85 vs 86, DocVQA ≈ 91 vs 94) while having had months less time to
stabilise in llama.cpp. It is a legitimate challenger, and the evaluation
harness exists to settle it with numbers rather than preference.

**Why not Qwen3.6.** Only 27B and 35B-A3B sizes exist — 18–23 GB at 4-bit,
which does not fit any instance within budget.

**Why llama.cpp and not vLLM or Ollama.** vLLM needs a GPU and the T4 lacks
bf16/FP8, so its advantages do not apply at this scale. Ollama hides the
JSON-schema and image-token controls this design depends on, and at the time of
writing does not support the newer Qwen vision projectors. `llama-server` gives
an OpenAI-compatible API *and* grammar-constrained decoding, which turned out to
be the single most important capability in the whole stack.

---

## 2. No separate OCR engine

**Chosen:** none. Qwen3-VL reads the card text directly.

**Rejected: EasyOCR.** It is a CRAFT + CRNN pipeline that Qwen3-VL outperforms
on scene and document text. Feeding its output to the model would add a worse
signal to a better reader. It also pulls in PyTorch — roughly 1 GB installed
and 800 MB resident — memory far better spent on a larger model.

**Rejected: Docling.** It converts PDF, DOCX and PPTX into structured Markdown
using layout models. Given a JPEG it delegates to an OCR engine anyway, so
"Docling + EasyOCR" is EasyOCR plus a large dependency tree.

**Still open:** RapidOCR (ONNX, ~15 MB, no PyTorch) as a *cross-check* rather
than an extractor — flagging an email or phone that does not appear in the OCR
text. It is worth adding only if evaluation shows a gap, and adding it before
that would be optimising against a guess.

---

## 3. Required-but-nullable fields in the grammar

**Chosen:** every data field is marked required in the JSON schema handed to
the model, and every field also permits `null`.

This is the decision that took accuracy from 82.1 % to 100 % on the card set.
Pydantic makes a defaulted field optional, and **an optional property in a
grammar is one the model may skip**. Observed directly: a 4B model emitted
`raw_text`, then `company`, then jumped to `notes` and degenerated into a
repetition loop, omitting the name, position, email and phone entirely.

Marking fields required forces an answer for each; permitting `null` means the
answer may legitimately be "not printed". The two together are what make
"never invent a value" enforceable rather than merely requested.

Validation stays lenient — defaults remain on the Pydantic model — so a hosted
provider returning a partial object is still accepted rather than discarded.
Generation is as constrained as possible; parsing is as forgiving as possible.

---

## 4. PostgreSQL for both leads and the queue

**Chosen:** PostgreSQL 16, with the task table as the queue via
`SELECT … FOR UPDATE SKIP LOCKED`.

**Rejected: Redis + Celery.** A second datastore and a broker to operate, for a
workload of at most fifty tasks per batch on one box.

**Rejected: SQLite.** Single-writer, and the API and worker are separate
processes. It is used for unit tests through the same SQLAlchemy models.

**Rejected: DynamoDB / MongoDB.** Leads are relational and are exported as a
table; there is nothing to gain from schema flexibility here.

**Leases, not acknowledgements.** A claimed task carries an expiry. A worker
that dies mid-card leaves a lease that lapses, and the task becomes claimable
again — a crash costs one retry rather than a lost card. The attempt counter is
incremented at *claim* time, so a card that reliably kills its worker cannot be
retried forever.

---

## 5. A three-tier chain with per-tier circuit breakers

**Chosen:** GPU → CPU → hosted Qwen API, each behind its own breaker.

A single model server is a single point of failure, and this is a demo that has
to stay up while being looked at. The breaker is what makes the fallback
*cheap*: without it a dead GPU container would cost every card in a batch a
full timeout before falling through, so fifty cards would rediscover the same
failure fifty times.

Only consecutive failures trip a breaker — one hard-to-read photo among
successes should not take a working tier out of service — and a failed
half-open probe restarts the cooldown rather than letting every subsequent card
retry a tier that is still down.

**The tier that answered is recorded per card** and shown in the UI and
workbook. A batch that spanned tiers has uneven accuracy, and presenting it as
uniform would be misleading.

---

## 6. Confidence as grounding, not probability

**Chosen:** a heuristic score asking whether a value survived validation and
appears in the text the model transcribed.

Token probabilities from a constrained decode are not calibrated and would be
actively misleading — the grammar forces high-probability tokens regardless of
whether the content is right. The grounding check answers a narrower but more
useful question, and it is what catches an invented field: a hallucinated
company name usually does not appear in the model's own transcription.

A phone number that libphonenumber cannot confirm has its confidence capped
even when it matches the transcription exactly, because the failure mode there
is a plausible-looking number that cannot be dialled.

---

## 7. Keep every printed phone number

**Chosen:** three validity levels — confirmed, possible, unparseable — instead
of a boolean.

Found while testing: libphonenumber's metadata lags telecom allocations, so
real dialable numbers fail `is_valid_number` while passing
`is_possible_number`. A Dubai landline was being silently discarded. For a
lead-extraction tool, losing a prospect's phone number to a validation library
is a serious failure.

Now a confirmed number is preferred as primary, a merely possible one is kept
with capped confidence, and an unparseable one is retained verbatim among the
extra phones but never promoted to the primary column. Selection also ranks
recognisability above label, so an unreadable "mobile" never displaces a
working office number.

---

## 8. Polling, not server-sent events

**Chosen:** the frontend polls job status every two seconds and stops when the
batch settles.

SSE is the more elegant answer and is a documented future option. Polling
behaves predictably behind a reverse proxy, survives a laptop sleeping and a
connection dropping, needs no reconnection logic, and its cost is one indexed
row read per two seconds per viewer. At this scale the simpler mechanism is
the better engineering.

---

## 9. One EC2 instance, two compose profiles

**Chosen:** a single instance running Docker Compose, with `gpu` and `cpu`
profiles over one compose file.

Kubernetes, ECS or autoscaling would all be defensible at a different scale and
are pure cost here. The two profiles exist because of the credit constraint:
the GPU profile serves the evaluation window, and the CPU profile keeps the URL
alive afterwards at roughly a tenth of the hourly cost. The API and worker
images are identical between them, so the switch is configuration rather than a
second deployment — and because the Elastic IP moves with it, the public URL
and its certificate survive unchanged.

---

## 10. Being explicit that free tier cannot run this

**Chosen:** state it in the README rather than imply the requirement was met.

No AWS free-tier-eligible instance can hold a Qwen VLM: they have 1–2 GB of RAM
and the smallest usable model needs about 3 GB with its vision projector. There
are also no free GPU hours. The assignment says "free-tier or equivalent", and
the honest reading is the new-account credit pool on an instance that can
actually run the model.

Stating the constraint, the cost per hour, and the mitigation is more useful to
an evaluator than a claim that quietly does not hold.
