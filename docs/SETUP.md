# Setup guide

From a fresh clone to a running system, in three sizes: a laptop for
development, a single cloud VM for production, and the operating notes that
matter once it is live. Every command here has been run as written.

- [1. Prerequisites](#1-prerequisites)
- [2. Local development](#2-local-development)
- [3. Production: the Compose stack](#3-production-the-compose-stack)
- [4. Deploy on AWS](#4-deploy-on-aws)
- [5. Deploy on Azure](#5-deploy-on-azure)
- [6. Configuration reference](#6-configuration-reference)
- [7. Troubleshooting](#7-troubleshooting)

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
make test                          # 192 backend + 7 frontend tests, no model needed
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

## 6. Configuration reference

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
| `VLM_CLOUD_API_KEY` | *(empty)* | Alibaba Model Studio key for tier 3. Empty disables the tier cleanly. |
| `WORKER_CONCURRENCY` | `2` | Cards in flight. Use `1` on a CPU-only host. |
| `LLAMA_THREADS` | `nproc` | Set by the bootstrap; llama.cpp mis-detects the CPU count inside a container. |
| `IMAGE_MAX_EDGE_PX` | `768` | Long-edge cap sent to the model — the dominant latency lever. |
| `RETENTION_DAYS` | `7` | Batches, leads and images older than this are deleted. |
| `APP_ACCESS_CODE` | *(empty)* | Optional passcode on the endpoints that cost inference time. |

## 7. Troubleshooting

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

**Frontend Dockerfile: `"/src": not found`.** The image must be built with the
repository root as context: `docker build -f frontend/Dockerfile .` — the
Makefile and CI both do.
