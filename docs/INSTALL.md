# Installation & Deployment

## Prerequisites

- Docker 24+ with Compose v2
- For Swarm deploy: external `traefik-hub` overlay network
- ~2 GB RAM available for the container (1.5 GB limit configured)

## Local development

```bash
git clone <repo-url> && cd <repo>
docker compose up --build
```

- UI: http://localhost:8000
- API health: http://localhost:8000/health

Environment variables (optional, see `.env.example`):

| Variable | Default | Purpose |
|---|---|---|
| `TTB_HOST` | `localhost` | Documented hostname for deploy docs |
| `TTB_WORKERS` | `1` | Batch OCR thread pool size (keep at 1 on small hosts) |
| `OMP_NUM_THREADS` | `1` | Limits Tesseract/OpenBLAS thread fan-out |

## Docker Swarm + Traefik (test environment)

DNS for `example.local` is pre-configured in the test environment — no DNS changes needed.

```bash
docker build -t ttb-label-verifier:latest .
docker stack deploy -c docker-stack.yml ttb
```

Smoke test:

```bash
curl -sf https://example.local/health
curl -sf -o /dev/null -w '%{http_code}\n' https://example.local/
```

### Traefik labels

The stack file routes HTTP and HTTPS to port 8000 with `certresolver=production`. A 1200s `serversTransport` timeout is configured so 300-image batch jobs (~15 min) are not cut off by the proxy. To deploy elsewhere, update the `Host()` rules in `docker-stack.yml` or set `TTB_HOST` and substitute in your templating.

Placement constraints (per internal Docker policy):

```yaml
placement:
  constraints:
    - node.labels.isolated != true
    - node.labels.gpu != true
```

## Generic deployment

Any Docker host:

```bash
docker build -t ttb-label-verifier .
docker run -p 8000:8000 --memory=1536m ttb-label-verifier
```

Push to your registry and run on Kubernetes, ECS, etc. — single container, no external dependencies.

## Troubleshooting

| Issue | Fix |
|---|---|
| First request slightly slower | Tesseract warm-up on startup; subsequent requests typically 2–5s |
| OOM during run | Do not raise `TTB_WORKERS` above 1 on memory-constrained hosts; keep 1.5 GB limit |
| 502 from Traefik | Wait for healthcheck `start-period` (30s) after deploy |
| Poor OCR on photos | Expected for glare/angle; use flat COLA artwork scans when possible |

## Evaluation

Requires local `test images/` (not in repository):

```bash
pip install requests
python scripts/evaluate.py --base-url http://localhost:8000
```

Use `--limit 5` to run a subset before a full benchmark.

## Pre-submit verification scripts

Prove latency and batch-volume requirements against the **running** container (not just the README):

```bash
pip install requests
docker compose up --build -d

# Per-label latency (first request after restart + warm second request)
python scripts/latency_check.py --base-url http://localhost:8000 --restart

# Generate 300 copied test images + CSV manifest (writes batch_test/, gitignored)
python scripts/generate_batch_fixture.py --count 300

# Single 300-image batch (~15 min); records docker memory if --docker
python scripts/batch_load_test.py --count 300 --docker

# Chunked fallback (6×50) for browser/proxy limits
python scripts/batch_load_test.py --count 300 --chunk-size 50 --docker
```

Measured on reference hardware (1.5 GB cap): first verify ~2.9s, warm ~2.8s, 300-batch peak RAM ~80 MiB, wall time ~14.6 min. See `batch_test/report.json` after a load test.
