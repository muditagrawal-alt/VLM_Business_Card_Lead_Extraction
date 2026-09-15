# Runbook

Operational procedures for the deployed service. Every command assumes you are
on the instance in `/opt/vlm-leads` unless stated otherwise.

```bash
cd /opt/vlm-leads
COMPOSE="docker compose -f deploy/docker-compose.prod.yml --profile gpu"
```

## Deploy

First boot is handled by `deploy/ec2/user-data-gpu.sh` as cloud-init user data.
It installs Docker if the AMI lacks it, verifies the GPU is visible **from
inside a container**, downloads the weights, and waits for `/api/v1/ready`
rather than reporting success as soon as containers start.

Subsequent deploys:

```bash
git pull --ff-only
$COMPOSE up -d --build
curl -fsS http://localhost/api/v1/ready
```

Migrations run as part of the API container's start command, so the schema and
the code that needs it move together.

## Roll back

```bash
git log --oneline -10
git checkout <previous-tag-or-sha>
$COMPOSE up -d --build
```

If the bad release included a migration, reverse it **before** switching code,
because the older code will not understand the newer schema:

```bash
$COMPOSE exec api alembic downgrade -1
```

CI applies, reverses and re-applies every migration, so a downgrade path is
known to exist before it is ever needed in anger.

## Check what is wrong

```bash
$COMPOSE ps                              # who is up, who is restarting
curl -s localhost/api/v1/ready | jq      # database + per-tier health
curl -s localhost/api/v1/stats | jq      # throughput, latency, breaker state
$COMPOSE logs --tail 100 worker
$COMPOSE logs --tail 100 llama-gpu
nvidia-smi                               # is the GPU actually in use
```

`/stats` is the first thing to read. It reports cards processed per tier — if
work is landing on `cpu` when the GPU should be serving, the GPU tier is
failing and its breaker has opened.

## Common failures

**Cards succeed but every one reports the `cpu` tier.** The GPU tier is down
and the chain is doing its job. Check `$COMPOSE logs llama-gpu` for an
out-of-memory kill, then `nvidia-smi` for VRAM. The 8B at Q8_0 needs roughly
12.5 GB of the T4's 15 GB; if something else is resident, drop to `Q6_K` or set
`LLAMA_ARG_N_PARALLEL=1`.

**`llama-gpu` restart-loops on boot.** Almost always a missing or truncated
vision projector: the model loads and then fails on the first image. Verify
with `ls -lh /opt/models` and re-run `scripts/download_models.sh`, which checks
GGUF magic bytes rather than merely that a file exists.

**Everything is ready but uploads return 429.** Rate limiting, working as
intended. Raise `RATE_LIMIT_JOBS` in `.env` and restart the API, or set
`APP_ACCESS_CODE` and share the code instead of loosening the limit.

**Cards stay `queued` and nothing moves.** The worker is not claiming. Check it
is running, then look for tasks stuck in `processing` with an expired lease —
those become claimable again automatically:

```bash
$COMPOSE exec postgres psql -U leads -d leads -c \
  "select status, count(*) from tasks group by status;"
```

**A single card fails repeatedly.** It has exhausted `TASK_MAX_ATTEMPTS`. The
error is on the row and shown in the UI, where it can be retried by hand; a
manual retry resets the attempt counter because it is a deliberate decision
rather than the automatic retry the counter exists to bound.

## Back up and restore

A nightly `pg_dump` runs in the `backup` container with a 14-day window.

```bash
$COMPOSE exec backup ls -lh /backups
```

Restore:

```bash
$COMPOSE stop api worker                       # stop writers first
gunzip -c /backups/leads-YYYYMMDD.sql.gz \
  | $COMPOSE exec -T postgres psql -U leads -d leads
$COMPOSE start api worker
```

Card images live in the `card_storage` volume and are **not** in the dump. A
restored database may reference images that are gone; those cards show a
missing-image error and can be re-uploaded. This is a deliberate trade-off —
the leads are the valuable artefact, the images are reproducible input.

## Cost control

This is the procedure that keeps the public URL alive to the end of the
evaluation window.

```bash
aws ce get-cost-and-usage --time-period Start=2026-09-01,End=2026-09-30 \
  --granularity MONTHLY --metrics UnblendedCost
```

g4dn.xlarge runs about **$0.54/hour all-in**, so $100 of credit is roughly
**7.5 days** of continuous uptime and $200 about 15.

- **During development**, stop the instance when idle. The EBS volume and
  Elastic IP still cost a little, but compute is the overwhelming majority.
- **Alarms** are set at $50 and $80 of spend. When the first fires, decide
  deliberately whether to keep the GPU running.
- **With roughly $40 of credit left**, switch to the CPU profile (below) so the
  URL outlives the GPU budget.
- **On a Free Plan account the account closes at $0** and every resource stops.
  If the URL must survive that, upgrade to a Paid plan *before* reaching it.

### Switch from GPU to the CPU profile

Accuracy drops from the 8B model to the 4B one and a card takes tens of
seconds instead of a few, but the service stays up at roughly a tenth of the
hourly cost.

```bash
# On the GPU instance: take a final dump and copy it off.
$COMPOSE exec postgres pg_dump -U leads -d leads | gzip > /tmp/final.sql.gz
scp ubuntu@<gpu-ip>:/tmp/final.sql.gz .

# Launch an 8 GB instance (m7i-flex.large or t4g.large) with
# deploy/ec2/user-data-cpu.sh, then move the Elastic IP across so the
# public URL does not change:
aws ec2 associate-address --instance-id <new-id> --allocation-id <eip-alloc-id>

# On the new instance, restore and start the CPU profile.
docker compose -f deploy/docker-compose.prod.yml --profile cpu up -d --build
gunzip -c final.sql.gz | docker compose -f deploy/docker-compose.prod.yml \
  exec -T postgres psql -U leads -d leads

# Finally, stop paying for the GPU.
aws ec2 stop-instances --instance-ids <gpu-id>
```

Because the Elastic IP moves with the service, the certificate and the URL are
unchanged and nobody holding the link needs to be told anything.

## Scheduled uptime

To stretch credits, run the instance only during working hours and say so in
the README rather than letting a reviewer meet a dead link:

```bash
aws events put-rule --name vlm-start --schedule-expression "cron(30 2 * * ? *)"
aws events put-rule --name vlm-stop  --schedule-expression "cron(30 18 * * ? *)"
```

## Data handling

- Batches, leads and images are deleted after `RETENTION_DAYS` (7 by default).
- A user can purge their own batch immediately: `DELETE /api/v1/jobs/{id}`.
- Uploads have EXIF stripped on ingest, so stored images carry no GPS trail.
- Client IP addresses are stored only as a salted hash.
- With `VLM_CLOUD_ENABLED=true`, a card that both self-hosted tiers fail is
  sent to Alibaba Model Studio in Singapore. Rows processed that way carry a
  badge in the UI saying the card left the server. Set it to `false` to keep
  everything on-box.
