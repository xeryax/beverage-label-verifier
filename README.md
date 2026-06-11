# TTB Label Verifier

AI-powered alcohol label verification prototype for TTB compliance agents. Upload label images (single or batch), compare seven mandatory fields against COLA application data, and get a color-coded pass / review / fail report in seconds.

**Live (test):** https://example.local

## Overview

Agents pull up a COLA application and check that label artwork matches the form: brand, class/type, ABV, net contents, producer, country of origin (imports), and the government health warning. This tool automates that comparison — it does **not** infer expected values from the image alone.

Regulatory rules are grounded in [TTB Labeling Resources](https://www.ttb.gov/regulated-commodities/labeling/labeling-resources) — see [docs/REGULATORY.md](docs/REGULATORY.md).

## Approach

### Workflow

1. Agent enters application data (or uploads a CSV manifest for batch jobs).
2. Agent uploads one or more label images (flat COLA artwork is the happy path).
3. Backend runs OCR → structured field extraction → fuzzy/numeric comparison per field.
4. UI shows per-field pass / review / fail badges and an overall verdict.

### Verdict semantics

| Overall | Meaning |
|---------|---------|
| **pass** | All checked fields matched within threshold |
| **review** | Likely OK but needs human eyes (low OCR confidence, optional beer/wine fields absent, producer not found, etc.) |
| **fail** | Clear mismatch (e.g. wrong ABV, title-case warning header, garbled warning on photo) |

Roll-up rule: any field **fail** → overall fail; else any **review** → overall review; else pass.

### Matching strategy (summary)

| Field | Strategy |
|-------|----------|
| Brand, class, producer, country | RapidFuzz; case-insensitive; warning text stripped from search haystack |
| Alcohol content | Parsed % vs application; tolerance by beverage type |
| Net contents | Volume + unit regex match |
| Government warning | Body fuzzy match + ALL CAPS header check (27 CFR § 16.21) |

Beer: ABV, producer, and country are surfaced as **review** when absent (optional for many malt beverages). Wine: table wine may omit ABV in the application — flagged **review**, not fail.

## Tools and technical choices

| Layer | Choice | Rationale |
|-------|--------|-----------|
| Backend | Python 3.11, FastAPI | Fast iteration, good OCR/ML ecosystem |
| OCR | Tesseract + OpenCV | Runs fully offline; ~50–150 MB RAM vs multi-GB for neural OCR |
| Matching | RapidFuzz | Handles case/punctuation drift (e.g. `STONE'S THROW` vs `Stone's Throw`) |
| Frontend | React + Vite | Simple agent UI; static build served by FastAPI |
| Deploy | Docker multi-stage image | Single container, no external services |
| Orchestration | Compose (local) + Swarm/Traefik (test) | Matches “deploy anywhere” + internal test host |

**Why Tesseract:** Stakeholders require sub-5s turnaround and no cloud APIs. Tesseract fits firewall-safe, memory-constrained hosts (single worker, 1.5 GB cap) while meeting latency targets on flat artwork (~2–5s after warm-up).

```
Upload → OCR (Tesseract + OpenCV) → Field extraction → Fuzzy/numeric match → Results
```

## Assumptions

- **Application data is provided** by the agent (JSON per image or CSV manifest). The tool compares image ↔ form; it does not replace COLA.
- **Standalone POC** — no live COLA integration, auth, or document retention.
- **English labels** only; TTB mandatory fields per 27 CFR parts 4, 5, 7, and 16.
- **OCR limits:** We verify warning *wording* and ALL CAPS header where readable; we cannot verify bold, minimum type size, or placement (27 CFR 16.22).
- **Flat artwork** is the design center; bottle photos may be slower and less accurate.
- **Evaluation harness** (`scripts/evaluate.py`) expects local test images and ground truth — not shipped in this repository (see `.gitignore`).

## Quick start

```bash
docker compose up --build
```

Open http://localhost:8000

## Requirements mapping

| Stakeholder need | How we address it |
|---|---|
| Sub-5s on routine flat labels | Tesseract + OpenCV preprocessing; single worker; flat artwork typically 2–5s |
| Simple UI for agents | Three-step layout: form → upload → results; large controls, plain-language badges |
| Batch uploads | Multi-image upload + optional CSV manifest |
| Fuzzy brand matching | RapidFuzz token_sort / partial_ratio (case/punctuation tolerant) |
| Government warning | ALL CAPS header check + body match to 27 CFR § 16.21 text |
| No cloud APIs | Tesseract + OpenCV run fully in-container (~1.5 GB RAM cap) |
| Deploy anywhere | `docker-compose.yml` (generic) + `docker-stack.yml` (Traefik Swarm) |

## API

```bash
curl -X POST http://localhost:8000/api/verify \
  -F "image=@label.png" \
  -F 'application={"beverageType":"spirits","brandName":"OLD TOM DISTILLERY","classType":"Kentucky Straight Bourbon Whiskey","alcoholContent":"45% Alc./Vol.","netContents":"750 mL","producer":"Old Tom Distillery, Louisville, KY","countryOfOrigin":"United States"}'

curl http://localhost:8000/health
```

See [docs/API.md](docs/API.md) for batch endpoint details.

## Evaluation harness

With the app running and local test assets available:

```bash
python scripts/evaluate.py --base-url http://localhost:8000
```

Use `--limit N` to run a subset. Requires `pip install requests`.

## Prior art and design influences

We reviewed public implementations before building:

| Project | Stack | What we learned |
|---|---|---|
| [JavierAvitia/ai-ttb-label-verifier](https://github.com/JavierAvitia/ai-ttb-label-verifier) | Streamlit + EasyOCR | RapidFuzz thresholds, warning caps-vote, class-inherits-brand, dual-pass OCR |
| [ryparker/excisely](https://github.com/ryparker/excisely) | Next.js + Tesseract/GCP | Stakeholder-to-feature mapping, CSV batch workflow, field-appropriate match strategies |
| [ArkieCoder/ttb-verifier](https://github.com/ArkieCoder/ttb-verifier) | FastAPI + Ollama + AWS | API-first design, Docker multi-stage build, regulatory tolerance docs |

**Differentiation:** Right-sized scope (verifier only), Tesseract for constrained hosts, reproducible eval harness, deploy-anywhere Docker (compose + Swarm/Traefik), firewall-safe local OCR, React agent UI.

## Trade-offs and limitations

- OCR cannot verify warning **bold**, font size, or placement (27 CFR 16.22)
- Photo labels on curved bottles may exceed 5s and degrade accuracy vs flat COLA scans
- No COLA system integration
- Sulfite declaration and age statement supported in matcher but not in the core seven-field UI
- Benchmark test images are not included in the repository

## Documentation

- [docs/INSTALL.md](docs/INSTALL.md) — deployment guide
- [docs/REGULATORY.md](docs/REGULATORY.md) — TTB rule citations
- [docs/API.md](docs/API.md) — REST API reference

## License

**All rights reserved.** This repository is shared for evaluation purposes.
Please do not reuse or redistribute without permission. See [LICENSE](LICENSE).

Third-party test images from [JavierAvitia/ai-ttb-label-verifier](https://github.com/JavierAvitia/ai-ttb-label-verifier) (when used locally) remain under their MIT license.
