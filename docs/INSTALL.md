# Installation & Deployment

## Prerequisites

- Docker 24+ with Compose v2
- For Swarm deploy: external `traefik-hub` overlay network
- ~4 GB RAM for OCR model load

## Local development

```bash
git clone <repo-url> && cd ttb
docker compose up --build
```

- UI: http://localhost:8000
- API health: http://localhost:8000/health

Environment variables (optional, see `.env.example`):

| Variable | Default | Purpose |
|---|---|---|
| `TTB_HOST` | `localhost` | Documented hostname for deploy docs |
| `TTB_WORKERS` | `4` | Batch OCR thread pool size |

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

The stack file routes HTTP and HTTPS to port 8000 with `certresolver=production`. To deploy elsewhere, update the `Host()` rules in `docker-stack.yml` or set `TTB_HOST` and substitute in your templating.

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
docker run -p 8000:8000 --memory=4g ttb-label-verifier
```

Push to your registry and run on Kubernetes, ECS, etc. — single container, no external dependencies.

## Troubleshooting

| Issue | Fix |
|---|---|
| First request slightly slower | Tesseract warm-up on startup; subsequent requests typically 2–5s |
| OOM during build/run | Allocate ≥4 GB RAM; models need ~2 GB peak |
| 502 from Traefik | Wait for healthcheck `start-period` (120s) after deploy |
| Poor OCR on photos | Expected for glare/angle; use flat COLA artwork scans when possible |

## Evaluation

```bash
python scripts/evaluate.py --base-url http://localhost:8000
```

Requires `requests` (`pip install requests`).
