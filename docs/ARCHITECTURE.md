# Architecture

## Components

```mermaid
flowchart LR
  U[Browser<br/>React SPA] -->|HTTPS| C[Caddy<br/>TLS · static · proxy]
  C -->|/api/v1| A[FastAPI]
  C -->|/| S[(built SPA)]
  A --> P[(PostgreSQL 16<br/>leads + task queue)]
  A --> F[(volume<br/>card images)]
  W[Worker process] --> P
  W --> F
  W -->|tier 1| G[llama-gpu<br/>Qwen3-VL-8B Q8_0]
  W -.->|tier 2| Cp[llama-cpu<br/>Qwen3-VL-4B Q4_K_M]
  W -.->|tier 3| M[Alibaba Model Studio<br/>qwen3-vl-plus]
  B[backup] --> P

  subgraph instance["One VM (EC2 or Azure)"]
    C; A; S; P; F; W; G; Cp; B
  end
```

Only Caddy is reachable from the internet. Both model servers listen on the
compose network alone, so nothing can reach inference without going through the
API's validation and rate limits.

The `cpu` profile is this diagram without `llama-gpu`. The API and worker
images are identical between profiles.

## Request flow: uploading a batch

```mermaid
sequenceDiagram
  participant U as Browser
  participant A as API
  participant DB as PostgreSQL
  participant FS as Storage
  participant W as Worker
  participant V as VLM tier

  U->>A: POST /jobs (multipart, 1..50 images)
  A->>A: validate by decoding · EXIF orient · strip metadata · downscale
  A->>DB: insert job
  loop each accepted file
    A->>DB: find image by SHA-256
    alt already stored
      A->>DB: reuse existing image row
    else new
      A->>FS: write image
      A->>DB: insert image
    end
    A->>DB: insert task (queued)
  end
  A-->>U: 201 {job_id, accepted, duplicates, rejected[]}

  loop until settled
    U->>A: GET /jobs/{id} (every 2s)
    A->>DB: read counters + tasks
    A-->>U: progress, per-card state, ETA
  end
```

A file that cannot be decoded is reported in `rejected[]` rather than failing
the batch. The response distinguishes *accepted* from *duplicates*, because a
duplicate costs no inference.

## Processing one card

```mermaid
sequenceDiagram
  participant W as Worker
  participant DB as PostgreSQL
  participant FS as Storage
  participant G as GPU tier
  participant C as CPU tier
  participant H as Hosted tier

  W->>DB: SELECT ... FOR UPDATE SKIP LOCKED
  DB-->>W: task (status=processing, lease set, attempts+1)
  W->>FS: read image
  W->>W: start heartbeat (extends the lease)

  W->>G: chat/completions (schema-constrained)
  alt GPU answers
    G-->>W: JSON extraction
  else GPU fails or its breaker is open
    W->>C: same request
    alt CPU answers
      C-->>W: JSON extraction
    else CPU also fails
      W->>H: same request
      H-->>W: JSON extraction
    end
  end

  W->>W: validate · normalise · score confidence
  W->>DB: insert lead, record tier/model/latency, mark done
  W->>DB: recompute job counters
```

If every tier fails, the task returns to the queue while attempts remain, and
is marked failed once they are exhausted so the batch can settle. A failed card
is retryable by hand from the UI.

## Why the queue lives in PostgreSQL

The claim query is the whole mechanism:

```sql
SELECT * FROM tasks
WHERE attempts < :max_attempts
  AND (status = 'queued'
       OR (status = 'processing' AND lease_until < now()))
ORDER BY created_at
LIMIT :n
FOR UPDATE SKIP LOCKED;
```

- `SKIP LOCKED` lets several workers claim disjoint rows without coordinating.
- The `lease_until` clause is crash recovery: a worker that dies mid-card
  leaves a lease that lapses, and the row becomes claimable again.
- `attempts` is incremented at claim time, not on failure, so a card that
  reliably kills its worker cannot be retried forever.

Job counters are recomputed from the tasks rather than incremented, because an
increment drifts — a card that fails once and then succeeds would be counted
twice.

## Data model

```mermaid
erDiagram
  JOBS ||--o{ TASKS : has
  JOBS ||--o{ LEADS : produces
  IMAGES ||--o{ TASKS : "read by"
  TASKS ||--o| LEADS : yields

  JOBS { uuid id; enum status; int total; int done; int failed; timestamp expires_at }
  IMAGES { uuid id; string sha256_unique; string storage_key; int width; int height }
  TASKS { uuid id; enum status; int attempts; timestamp lease_until; enum provider; int latency_ms; jsonb raw_response }
  LEADS { uuid id; string first_name; string last_name; string position; string company; string location; string phone; string email; jsonb confidence; bool edited_by_user }
```

`images` is keyed by content hash, so the same card uploaded twice is stored
once and reuses its extraction. Deleting a job cascades to its tasks and leads;
images are shared and left to the retention sweep.

## Trust boundaries

| Boundary | Treatment |
|---|---|
| Uploaded file | Validated by decoding, never by declared type or extension. Pixel cap against decompression bombs. |
| Card text | Data, never instruction. Grammar-constrained output means text on a card cannot change the response shape. |
| Model output | Validated against the schema; phones and emails re-validated independently. |
| User corrections | Normalised through the same code as extracted values. |
| Client IP | Stored only as a salted hash. |
| Hosted tier | Off-box. Disclosed per row in the UI and disableable entirely. |
