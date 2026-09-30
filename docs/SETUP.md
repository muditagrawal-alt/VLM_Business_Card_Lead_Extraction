# Setup guide

From a fresh clone to a running system, in three sizes: a laptop for
development, a single cloud VM for production, and the operating notes that
matter once it is live. Every command here has been run as written.

- [1. Prerequisites](#1-prerequisites)
- [2. Local development](#2-local-development)
- [3. Production: the Compose stack](#3-production-the-compose-stack)
- [4. Deploy on AWS](#4-deploy-on-aws)
- [5. Deploy on Azure](#5-deploy-on-azure)
- [6. Without a GPU: the hosted tier](#6-without-a-gpu-the-hosted-tier)
- [7. Guardrails for a public URL](#7-guardrails-for-a-public-url)
- [8. Configuration reference](#8-configuration-reference)
- [9. Troubleshooting](#9-troubleshooting)

---

## 1. Prerequisites

| Tool | Version | Used for |
|---|---|---|
| Docker + Compose plugin | 24+ | PostgreSQL locally; the whole stack in production |
| Python | 3.12 | API and worker |
| [uv](https://docs.astral.sh/uv/) | any | Python environment and lockfile |
| Node.js | 22 | Frontend build and tests |
| [llama.cpp](https://github.com/ggml-org/llama.cpp) | recent | Local model server (`llama-server`) — `brew install llama.cpp` on macOS |
| curl | any | Model download (the `hf` CLI is used if present, not required) |

**Hardware for local inference.** The 4B model (CPU tier) needs about 4 GB of
free RAM with its vision projector; the 8B model needs about 10 GB and is only
worth running locally with GPU or Apple-Silicon Metal offload. Development
does not require a model at all — the API and worker start without one, and
the test suite stubs inference.

**Disk.** Weights are ~13 GB for both models, ~3.5 GB for just the 4B
(`TIERS=cpu`). They live in `models/` locally and `/opt/models` on a server,
outside the checkout, so a redeploy never re-downloads them.

## 2. Local development

```bash
git clone https://github.com/muditagrawal-alt/VLM_Business_Card_Lead_Extraction.git
cd VLM_Business_Card_Lead_Extraction
cp .env.example backend/.env       # defaults are correct for local use
make install                       # uv venv + pip install -e ".[dev]"; npm install
```

Start PostgreSQL and apply the schema:

```bash
make db-up                         # PostgreSQL 16 on 127.0.0.1:5433
make migrate                       # alembic upgrade head
```

Download at least the CPU-tier model and serve it:

```bash
TIERS=cpu make models              # 4B Q4_K_M + projector, resumable
make llama-cpu                     # llama-server on 127.0.0.1:18081
```

In a second terminal, run the application:

```bash
make dev                           # API :8000, worker, Vite :5173
```

Open <http://localhost:5173>. Drop a few card images on the upload area; the
progress panel shows each card moving through the queue, and the results
table fills in as the worker finishes them. The API documents itself at
<http://localhost:8000/api/docs>, and `GET /api/v1/ready` reports the database
and every inference tier's health — check it first if a card sits in *queued*.

With only the CPU server running, `ready` reports the GPU tier as unavailable
and the chain skips it; set `VLM_GPU_ENABLED=false` in `backend/.env` to stop
it being probed at all. If you also have a Metal-capable Mac or a CUDA GPU:

```bash
make models                        # adds the 8B Q8_0
make llama-gpu                     # 127.0.0.1:18080, -ngl 99
```

### Verify

```bash
make test                          # 220 backend + 12 frontend tests, no model needed
make lint                          # ruff, ruff format, pyright, eslint, tsc
make eval                          # field accuracy against eval/cards (needs llama-cpu)
```

`make eval` writes `eval/results/latest-4b.json`; compare it against the
committed runs in that directory. `docs/EVALUATION.md` explains what the card
set does and does not measure.

### Ports

| Service | Port | Why not the default |
|---|---|---|
| PostgreSQL | 5433 | A locally installed PostgreSQL on 5432 silently wins the connection over Docker's bind and produces a misleading "role does not exist". |
| GPU model server | 18080 | 8080 is contested by Airflow, Spark, Tomcat and others. |
| CPU model server | 18081 | Same. |
| API | 8000 | — |
| Frontend | 5173 | Vite default. |

Production is unaffected: Compose addresses services by container name on an
internal network and only Caddy listens on the host.

## 3. Production: the Compose stack

One VM runs everything through `deploy/docker-compose.prod.yml`:

| Service | Image | Role |
|---|---|---|
| `caddy` | `frontend/Dockerfile` (Caddy 2 + built SPA) | TLS (Let's Encrypt), static files, proxies `/api` |
| `api` | `backend/Dockerfile` | FastAPI; runs migrations on start |
| `worker` | same image | Claims queued cards, calls the model chain, writes leads |
| `postgres` | postgres:16 | Leads, batches, and the work queue |
| `llama-gpu` | llama.cpp CUDA server | Tier 1: Qwen3-VL-8B Q8_0 (`gpu` profile only) |
| `llama-cpu` | llama.cpp server | Tier 2: Qwen3-VL-4B Q4_K_M |
| `backup` | postgres:16 | Nightly `pg_dump` to a volume |

Two profiles select the shape: `--profile gpu` runs all of the above,
`--profile cpu` omits `llama-gpu`. The API and worker images are identical
between them, so a GPU deployment and a CPU deployment differ only in
configuration.

**No domain is needed.** The site address uses [sslip.io](https://sslip.io),
which resolves `anything-20-80-103-145.sslip.io` to `20.80.103.145`, and Caddy
obtains a real Let's Encrypt certificate for it. Set `SITE_ADDRESS` to that
name (a comma-separated list is accepted) and `ACME_EMAIL` to yours.

**`.env` is mandatory and lives at `/opt/vlm-leads/.env`.** Compose derives
its project directory from the compose file's location, so `--env-file` must
be passed explicitly — the Makefile and bootstrap scripts always do. The
minimum for production:

```bash
SITE_ADDRESS=muditagrawal-20-80-103-145.sslip.io, 20-80-103-145.sslip.io
ACME_EMAIL=you@example.com
POSTGRES_PASSWORD=$(openssl rand -hex 24)
VLM_CLOUD_API_KEY=                 # optional; empty disables the hosted tier
```

The bootstrap scripts append `MODELS_DIR` and `LLAMA_THREADS` (from `nproc`)
themselves.

**Firewall.** Inbound 80 and 443 from anywhere (Caddy needs 80 for the ACME
challenge), 22 from your own address only. Nothing else listens on the host.

## 4. Deploy on AWS

Both scripts under `deploy/ec2/` are idempotent: re-running one updates the
checkout and restarts the stack.

### CPU profile — free-tier-equivalent

The smallest instance that runs the 4B model comfortably is 8 GB of RAM
(`m7i-flex.large` or `t4g.large`, ~$0.10/h). Ubuntu 24.04.

```bash
# 1. Launch: Ubuntu 24.04, 8 GB RAM, 40 GB gp3, a security group as above,
#    and an Elastic IP so the URL survives a stop/start.
# 2. Copy the .env in, then run the bootstrap.
scp -i key.pem .env ubuntu@<ip>:/tmp/.env
ssh -i key.pem ubuntu@<ip> 'sudo mkdir -p /opt/vlm-leads && sudo mv /tmp/.env /opt/vlm-leads/.env'
ssh -i key.pem ubuntu@<ip> 'curl -fsSL https://raw.githubusercontent.com/muditagrawal-alt/VLM_Business_Card_Lead_Extraction/main/deploy/ec2/user-data-cpu.sh | sudo bash'
```

The script sets `VLM_GPU_ENABLED=false`, `WORKER_CONCURRENCY=1` and
`TIERS=cpu`, downloads only the 4B weights, starts the `cpu` profile and waits for `/api/v1/ready`. Expect
~15 minutes on first boot, most of it the model download and image build.

### GPU profile — `g4dn.xlarge`

Use the **Deep Learning Base OSS NVIDIA Driver AMI**: it ships a matched
driver, Docker and the container toolkit, and installing those by hand is the
most common way a GPU deployment fails. Then the same three commands with
`user-data-gpu.sh`. The script refuses to continue unless `nvidia-smi` works
*inside a container*, so a driver problem surfaces immediately rather than as
every card silently falling through to the CPU tier.

**Quota.** A new account has zero G-instance vCPUs (`L-DB2E81BA`). Request 4
through Service Quotas before launching; first-line support routinely declines
new accounts and a reply on the case with a concrete justification is what
gets it escalated. Free-Plan accounts additionally cannot change instance type
or launch anything outside six free-tier types until upgraded.

## 5. Deploy on Azure

The equivalent instance is `Standard_NC4as_T4_v3` (4 vCPU, 28 GB, one T4,
~$0.59/h). Azure's stock Ubuntu image ships neither the NVIDIA driver nor the
container toolkit, so `deploy/azure/bootstrap-gpu.sh` installs both and then
runs the EC2 script unchanged.

```bash
az group create -n vlm-leads-rg -l centralus
az network vnet create -g vlm-leads-rg -n vlm-vnet --address-prefix 10.10.0.0/16 \
  --subnet-name app --subnet-prefix 10.10.1.0/24
az network nsg create -g vlm-leads-rg -n vlm-nsg
az network nsg rule create -g vlm-leads-rg --nsg-name vlm-nsg -n ssh  --priority 100 \
  --protocol Tcp --source-address-prefixes <your-ip>/32 --destination-port-ranges 22 --access Allow
az network nsg rule create -g vlm-leads-rg --nsg-name vlm-nsg -n web  --priority 110 \
  --protocol Tcp --source-address-prefixes Internet --destination-port-ranges 80 443 --access Allow
az network vnet subnet update -g vlm-leads-rg --vnet-name vlm-vnet -n app --network-security-group vlm-nsg
az network public-ip create -g vlm-leads-rg -n vlm-ip --sku Standard --allocation-method Static

az vm create -g vlm-leads-rg -n vlm-gpu -l centralus \
  --size Standard_NC4as_T4_v3 --image Canonical:ubuntu-24_04-lts:server:latest \
  --security-type Standard --os-disk-size-gb 100 --storage-sku StandardSSD_LRS \
  --admin-username ubuntu --ssh-key-values ~/.ssh/id_rsa.pub \
  --public-ip-address vlm-ip --vnet-name vlm-vnet --subnet app --nsg ""
az vm extension set -g vlm-leads-rg --vm-name vlm-gpu \
  --publisher Microsoft.HpcCompute --name NvidiaGpuDriverLinux
```

`--security-type Standard` matters: Trusted Launch enables Secure Boot, which
refuses the unsigned NVIDIA kernel module. The driver extension compiles the
module with DKMS after it reports success, so wait for `dkms status` to say
`installed`, reboot, and confirm `nvidia-smi` before continuing. Then:

```bash
scp .env ubuntu@<ip>:/tmp/.env
ssh ubuntu@<ip> 'sudo mkdir -p /opt/vlm-leads && sudo mv /tmp/.env /opt/vlm-leads/.env'
ssh ubuntu@<ip> 'curl -fsSL https://raw.githubusercontent.com/muditagrawal-alt/VLM_Business_Card_Lead_Extraction/main/deploy/azure/bootstrap-gpu.sh | sudo bash'
```

**Quota.** The T4 family starts at zero on a new subscription, a Free Trial
subscription cannot request it at all, and the self-service increase is
refused in every region. After upgrading to Pay-As-You-Go, a *Service and
subscription limits* support request for `Standard NCASv3_T4 Family vCPUs` → 4
is free on every plan and was approved in about fifteen minutes.

## 6. Without a GPU: the hosted tier

Tier 3 is any OpenAI-compatible vision endpoint, and it can carry the whole
load on its own. Two providers have been set up:

| Provider | `VLM_CLOUD_BASE_URL` | `VLM_CLOUD_MODEL` | Free allowance | Measured |
|---|---|---|---|---|
| Google AI Studio (Gemini) | `https://generativelanguage.googleapis.com/v1beta/openai/` | `gemini-3.6-flash,gemini-3.1-flash-lite` | Free tier, no card, no expiry; per-model rate limits | 100 % on the evaluation set, 16.6 s p50 |
| Alibaba Model Studio (Qwen) | `https://dashscope-intl.aliyuncs.com/compatible-mode/v1` | `qwen3-vl-plus` | 1M tokens per model for 90 days, Singapore endpoint only | Not measured |

A Gemini key comes from <https://aistudio.google.com/apikey>. Do not add
billing to the project if you want to stay on the free tier. The key belongs in
`.env` as `VLM_CLOUD_API_KEY` — never on a command line, where it lands in the
process list and shell history. The model list is tried in order: free tiers
retire models (`gemini-2.5-flash` returns 404 to new accounts) and shed load per
model, so a second name keeps the tier answering.

**The privacy trade-off.** On Gemini's free tier, Google may use prompts and
responses to improve its products, and human reviewers may read them. For a
demonstration on sample cards that is acceptable. For real contacts, use the
paid tier, where that does not apply, or keep the hosted tier disabled.

To run with no model server at all — nothing to download, about a gigabyte of
memory — disable the two self-hosted tiers and use the `hosted` profile:

```bash
# in .env
VLM_GPU_ENABLED=false
VLM_CPU_ENABLED=false
VLM_CLOUD_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/
VLM_CLOUD_MODEL=gemini-3.6-flash,gemini-3.1-flash-lite
VLM_CLOUD_API_KEY=...

make deploy-hosted
```

### The live deployment: frontend on Vercel, API on Oracle Cloud

The free deployment splits the two halves. Vercel serves the built SPA, and an
Oracle Cloud Always Free Ampere A1 instance runs the hosted profile. The
browser calls the API directly rather than through Vercel's proxy: the proxy's
limit on request size is not documented, which a 50-card upload could hit,
and every visitor would reach the API from Vercel's addresses, collapsing the
per-address limits into one shared bucket.

**Oracle.** Create an instance with image *Canonical Ubuntu 24.04* and shape
*VM.Standard.A1.Flex*. 1 OCPU / 6 GB is ample for this profile and far easier to
place than 2 / 12 when a region is short of ARM capacity. Open ports 80 and 443
in the subnet's security list, then:

```bash
scp .env ubuntu@<ip>:/tmp/.env
ssh ubuntu@<ip> 'sudo mkdir -p /opt/vlm-leads && sudo mv /tmp/.env /opt/vlm-leads/.env'
ssh ubuntu@<ip> 'curl -fsSL https://raw.githubusercontent.com/muditagrawal-alt/VLM_Business_Card_Lead_Extraction/main/deploy/oracle/bootstrap.sh | sudo bash'
```

`deploy/oracle/bootstrap.sh` also opens 80 and 443 in the instance's own
firewall: Oracle's Ubuntu images reject everything but SSH in iptables,
independently of the security list, and without that Let's Encrypt's challenge
never arrives. It installs Docker, forces the self-hosted tiers off and waits
until the API reports ready. Every compiled dependency ships an arm64 wheel,
so the images build unchanged.

**Vercel.** From `frontend/`, with the Vercel CLI logged in:

```bash
vercel project add <name>
vercel link --yes --project <name>
printf 'https://<api-host>' | vercel env add VITE_API_BASE_URL production
vercel deploy --prod
```

Importing the repository in the dashboard works as well: Root Directory
`frontend`, and the same variable. It is read at build time, and the build
writes a Content-Security-Policy that allows exactly that API origin.

**Connect them.** Set `APP_CORS_ORIGINS=https://<name>.vercel.app` in the
server's `.env` and recreate the API container:

```bash
docker compose --env-file .env -f deploy/docker-compose.prod.yml --profile hosted up -d api
```

## 7. Guardrails for a public URL

There are no accounts, so anything the URL exposes is exposed to whoever has
it. What stops a stranger from reading other people's leads, deleting them, or
spending the hosted key's quota:

| Guardrail | What it stops | Setting |
|---|---|---|
| No endpoint lists batches | Enumerating everyone's leads. A batch is reachable only by its id, an unguessable UUID the uploader's browser keeps. | — |
| Access code on upload, retry and delete | Strangers spending inference or emptying the database. Constant-time comparison. | `APP_ACCESS_CODE` |
| Per-address batch limit | A burst of uploads from one address. | `RATE_LIMIT_JOBS` |
| Per-address card limit | Many small batches adding up to hundreds of cards an hour. | `RATE_LIMIT_IMAGES_PER_HOUR` |
| Daily hosted-request ceiling | Draining the key's quota, however many addresses are involved. Shared by every worker, survives restarts. | `VLM_CLOUD_DAILY_REQUEST_LIMIT` |
| Cached health probes | Using the public `/api/v1/ready` to send traffic to the provider on your key. | `PROVIDER_HEALTH_INTERVAL_S` |
| Key stays server-side | Only the server processes hold the key; it is never sent to the browser or written to the logs. | — |

With `APP_ACCESS_CODE` set, share the link as `https://<host>/?code=<code>`.
The page stores the code, removes it from the address bar, and sends it with
every request; anyone without it is asked for it before an upload is sent.
Reading a batch, and its export, stay open to whoever holds the batch id.

Two things only the key's owner can do, in the provider's console: restrict
the key to the one API it needs, and rotate it immediately if it is ever
exposed.

## 8. Configuration reference

Every setting is an environment variable with a safe default;
[`.env.example`](../.env.example) documents all of them. The ones that change
behaviour in production:

| Variable | Default | Notes |
|---|---|---|
| `SITE_ADDRESS` | — | Required. Host name(s) Caddy serves and requests certificates for. |
| `ACME_EMAIL` | — | Required. Let's Encrypt expiry notices go here. |
| `POSTGRES_PASSWORD` | — | Required. |
| `VLM_GPU_ENABLED` | `true` | `false` on a CPU host, so the chain does not spend a timeout probing an absent server. |
| `VLM_CPU_TIMEOUT_S` | `600` | Per-card ceiling on the CPU tier. A 2-vCPU host needs the full value. |
| `VLM_CLOUD_BASE_URL` · `VLM_CLOUD_MODEL` | Model Studio · `qwen3-vl-plus` | Any OpenAI-compatible endpoint; a comma-separated model list is tried in order. |
| `VLM_CLOUD_API_KEY` | *(empty)* | Key for the hosted tier. Empty disables the tier cleanly. |
| `VLM_CLOUD_MAX_RETRIES` | `2` | Retries on 429 and 5xx, honouring `Retry-After`, before failing over to the next model. |
| `VLM_CLOUD_DAILY_REQUEST_LIMIT` | `1000` | Hosted requests per UTC day across all workers. Keep it below the provider's own cap; `0` disables it. |
| `WORKER_CONCURRENCY` | `2` | Cards in flight. Use `1` on a CPU-only host. |
| `LLAMA_THREADS` | `nproc` | Set by the bootstrap; llama.cpp mis-detects the CPU count inside a container. |
| `IMAGE_MAX_EDGE_PX` | `768` | Long-edge cap sent to the model — the dominant latency lever. |
| `RETENTION_DAYS` | `7` | Batches, leads and images older than this are deleted. |
| `APP_ACCESS_CODE` | *(empty)* | Optional passcode for uploading, retrying and deleting. Share as `/?code=…`. |
| `APP_CORS_ORIGINS` | *(empty)* | Browser origins allowed to call the API, comma-separated: the static host when the SPA is served there. |
| `VITE_API_BASE_URL` | *(empty)* | Frontend build only: the API's origin when the SPA is hosted elsewhere. |
| `RATE_LIMIT_JOBS` | `5/10minutes` | Batches per client address. |
| `RATE_LIMIT_IMAGES_PER_HOUR` | `100` | Cards per client address per hour. |

## 9. Troubleshooting

**`variable is not set` / `is missing a value` from Compose.** `--env-file .env`
was omitted. Compose looked for `deploy/.env`. Use the Makefile targets or the
`COMPOSE=` alias from the runbook.

**`FATAL: /opt/vlm-leads/.env is missing`.** The bootstrap runs after the
`.env` is copied in, not before. Copy it, re-run.

**All tiers failed: `could not connect to http://…:18081/v1`.** The model
server is not up or not reachable at that address. `ready` shows which tier
is unhealthy; locally, check the `llama-server` terminal.

**`timed out after 600s` on the CPU tier.** The host is too small for the
concurrency. Set `WORKER_CONCURRENCY=1`, confirm `LLAMA_THREADS` equals the
vCPU count, and check nothing else is pegging the CPU.

**`role "leads" does not exist` locally.** A native PostgreSQL on 5432 answered
instead of Docker's. The default `DATABASE_URL` uses 5433 for this reason;
check `backend/.env` was copied from `.env.example`.

**`docker run --gpus all … nvidia-smi` fails but `nvidia-smi` works.** The
container toolkit is missing or Docker was not restarted after
`nvidia-ctk runtime configure`. The Azure bootstrap does both; on AWS, use the
Deep Learning Base AMI.

**Certificate not issued.** Port 80 must be reachable from the internet for
the ACME challenge, and `SITE_ADDRESS` must resolve to this host. `docker
compose logs caddy` names the failing step.

**Cards fail with `daily limit of N hosted requests reached`.** The ceiling did
its job. It resets at 00:00 UTC. Today's count is in the database:
`select * from usage_counters order by day desc limit 3;`. Raise
`VLM_CLOUD_DAILY_REQUEST_LIMIT` only if the provider's own daily cap allows it.

**Hosted tier fails with `HTTP 404`.** The model has been retired for your
account. List what the key can use with `GET <base-url>/models`, and put a
current name first in `VLM_CLOUD_MODEL`.

**Uploads return 401 `a valid access code is required`.** `APP_ACCESS_CODE` is
set. Open the site through the `/?code=…` link, or enter the code when asked.

**The Vercel page loads, but every request fails with a CORS error.** The API
does not list the page's origin. Set `APP_CORS_ORIGINS` to it exactly (scheme
and host, e.g. `https://app.vercel.app`) and recreate the `api` container;
`docker compose … exec api env | grep CORS` shows what the container actually
received.

**Oracle refuses the instance with `Out of host capacity`.** The region is short
of ARM hosts. Try another availability domain, a smaller shape (1 OCPU / 6 GB is
enough), or retry later.

**Frontend Dockerfile: `"/src": not found`.** The image must be built with the
repository root as context: `docker build -f frontend/Dockerfile .` — the
Makefile and CI both do.
