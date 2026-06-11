# TTB Label Verifier

AI-powered alcohol label verification prototype for TTB compliance agents. Upload label images (single or batch), compare seven mandatory fields against COLA application data, and get a color-coded pass / review / fail report in seconds.

**Live (test):** https://example.local

## Overview

This tool helps agents verify that label artwork matches application form data:

- Brand name, class/type, alcohol content, net contents
- Producer/bottler name and address
- Country of origin (imports)
- Government health warning (27 CFR Part 16)

Regulatory rules are grounded in [TTB Labeling Resources](https://www.ttb.gov/regulated-commodities/labeling/labeling-resources) — see [docs/REGULATORY.md](docs/REGULATORY.md).

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
| Batch uploads | Multi-image upload + optional CSV manifest (`sample-data/manifest.csv` format) |
| Fuzzy brand matching | RapidFuzz token_sort_ratio (case/punctuation tolerant) |
| Exact government warning | ALL CAPS header check + body match to 27 CFR § 16.21 text |
| No cloud APIs | Tesseract + OpenCV run fully in-container (~1.5 GB RAM cap) |
| Deploy anywhere | `docker-compose.yml` (generic) + `docker-stack.yml` (Traefik Swarm) |

## Architecture

- **Backend:** Python 3.11, FastAPI, Tesseract, OpenCV, RapidFuzz
- **Frontend:** React + Vite (static files served by FastAPI)
- **Deploy:** Single Docker image (~2 GB with OCR models baked in)

```
Upload → OCR (Tesseract + OpenCV) → Field extraction → Fuzzy/numeric match → Results
```

## API

```bash
# Single label
curl -X POST http://localhost:8000/api/verify \
  -F "image=@label.png" \
  -F 'application={"beverageType":"spirits","brandName":"OLD TOM DISTILLERY","classType":"Kentucky Straight Bourbon Whiskey","alcoholContent":"45% Alc./Vol.","netContents":"750 mL","producer":"Old Tom Distillery, Louisville, KY","countryOfOrigin":"United States"}'

# Health
curl http://localhost:8000/health
```

See [docs/API.md](docs/API.md) for batch endpoint details.

## Evaluation harness

With the app running:

```bash
python scripts/evaluate.py --base-url http://localhost:8000
```

Or in-process (inside container):

```bash
docker compose run --rm -v "$(pwd)/test images:/test images" -v "$(pwd)/scripts:/scripts" ttb \
  python /scripts/evaluate.py --direct
```

## Prior art & design influences

We reviewed public take-home implementations before building:

| Project | Stack | What we learned |
|---|---|---|
| [JavierAvitia/ai-ttb-label-verifier](https://github.com/JavierAvitia/ai-ttb-label-verifier) | Streamlit + EasyOCR | RapidFuzz thresholds, warning caps-vote, class-inherits-brand, dual-pass OCR |
| [ryparker/excisely](https://github.com/ryparker/excisely) | Next.js + Tesseract/GCP | Stakeholder-to-feature mapping, CSV batch workflow, field-appropriate match strategies |
| [ArkieCoder/ttb-verifier](https://github.com/ArkieCoder/ttb-verifier) | FastAPI + Ollama + AWS | API-first design, Docker multi-stage build, regulatory tolerance docs |

**Our differentiation:** Right-sized scope (verifier only, no full COLA portal), reproducible 27-case eval harness, deploy-anywhere Docker (compose + Swarm/Traefik), firewall-safe local OCR, React agent UI without auth friction.

## Limitations

- OCR cannot verify warning **bold**, font size, or placement (27 CFR 16.22)
- English-language labels only
- Photo labels on curved bottles may exceed 5s and degrade OCR accuracy
- No COLA system integration (standalone POC)
- Sulfite declaration and age statement not in core seven-field scope

## Documentation

- [docs/INSTALL.md](docs/INSTALL.md) — deployment guide
- [docs/REGULATORY.md](docs/REGULATORY.md) — TTB rule citations
- [docs/API.md](docs/API.md) — REST API reference

## License

MIT. External test images in `test images/external/javieravitia/` are MIT-licensed from [JavierAvitia/ai-ttb-label-verifier](https://github.com/JavierAvitia/ai-ttb-label-verifier).
