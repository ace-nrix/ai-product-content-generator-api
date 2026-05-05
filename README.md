# AI Product Content Generator API

> **Author:** Noah Rix  
> **Repo:** https://github.com/ace-nrix/ai-product-content-generator-api  
> **Deployment target:** Databricks Apps

A FastAPI service that generates structured product content for retail catalogs. Given a UPC, manufacturer number, and/or brand name, it:

1. **Searches Tavily** for raw product specifications and marketing copy from across the web (excluding acehardware.com).
2. **Passes the results to Anthropic Claude** which restructures the raw search data into typed JSON content objects driven by swappable config files.

Content types include romance text, feature lists, SEO descriptions, attribute population with UOM conversion, and an "everything" bundle.

---

## Project Structure

```
ai-product-content-generator-api/
├── app.py                    # FastAPI application entrypoint
├── app.yaml                  # Databricks Apps deployment manifest
├── requirements.txt
├── .env                      # Local secrets (git-ignored)
├── tavily_service.py         # Tavily Search API wrapper
├── anthropic_service.py      # Anthropic Claude wrapper + batch extraction
├── routers/
│   ├── __init__.py
│   └── main.py               # All route definitions + shared pipeline
├── json-structures/          # Content type config files (schema + prompts)
│   ├── romance-text.json
│   ├── romance-text-3.json
│   ├── features.json
│   ├── features-10.json
│   ├── search-description.json
│   ├── attributes.json
│   └── everything.json
└── deploy/
    ├── dev.ps1               # Local dev launcher (uvicorn + hot-reload)
    ├── killports.ps1         # Free port 8000 if already in use
    └── deploy.ps1            # Upload to Databricks Workspace + deploy App
```

---

## Prerequisites

- Python 3.11+
- [Tavily API key](https://tavily.com) — set as `TAVILY_API_TOKEN`
- [Anthropic API key](https://console.anthropic.com) — set as `ANTHROPIC_API_TOKEN`
- Databricks CLI (for deployment)

---

## Local Development

### 1. Clone & install

```bash
git clone https://github.com/ace-nrix/ai-product-content-generator-api.git
cd ai-product-content-generator-api
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure secrets

Copy `.env` and fill in your keys:

```
TAVILY_API_TOKEN=tvly-prod-...
ANTHROPIC_API_TOKEN=sk-ant-...
```

### 3. Run

```powershell
.\deploy\dev.ps1
```

Or directly:

```bash
uvicorn app:app --host 0.0.0.0 --port 8000 --reload
```

API docs available at **http://localhost:8000/docs**.

---

## API Endpoints

All endpoints accept a **JSON array** of product items, supporting one or many products per request. Tavily is queried sequentially per item (API limitation); Anthropic extractions are dispatched concurrently.

### Standard endpoints — `POST /romance-text` · `/romance-text-3` · `/features` · `/features-10` · `/search-description` · `/everything`

**Request body:**

```json
[
    { "upc": "751492779010", "mfg": "VCG4060T16DFXPB1", "brand": "PNY" },
    { "upc": "012345678901", "mfg": "SOME-MODEL-NUM", "brand": "Acme" }
]
```

| Field   | Type   | Description                     |
| ------- | ------ | ------------------------------- |
| `upc`   | string | Product UPC / EAN code          |
| `mfg`   | string | Manufacturer item / part number |
| `brand` | string | Brand or manufacturer name      |

### Attribute endpoint — `POST /attributes`

**Request body:**

```json
[
    {
        "upc": "751492779010",
        "mfg": "VCG4060T16DFXPB1",
        "brand": "PNY",
        "attributeName": "Card Width",
        "inputUOM": "inches",
        "validValues": ["1.5 in", "1.57 in", "2 in", "2.5 in"]
    }
]
```

| Field           | Type            | Description                                                |
| --------------- | --------------- | ---------------------------------------------------------- |
| `upc`           | string          | Product UPC / EAN code                                     |
| `mfg`           | string          | Manufacturer item / part number                            |
| `brand`         | string          | Brand name                                                 |
| `attributeName` | string          | Attribute to populate (e.g. `"Width"`, `"Wattage"`)        |
| `inputUOM`      | string          | Required output unit (e.g. `"inches"`, `"lbs"`, `"watts"`) |
| `validValues`   | `array<string>` | List of allowed values Anthropic must select from          |

Anthropic converts the found value to `inputUOM` if needed before matching it to `validValues`. For example, if Tavily finds `"40mm"` but `inputUOM` is `"inches"` and `validValues` contains `"1.57 in"`, Claude returns `"1.57 in"`.

---

## Content Type Configs (`json-structures/`)

Each JSON file drives one endpoint. Editing a file immediately changes the output schema and Anthropic prompt — no code change required.

| File                      | Endpoint              | Output                                          |
| ------------------------- | --------------------- | ----------------------------------------------- |
| `romance-text.json`       | `/romance-text`       | Single 2-3 sentence marketing paragraph         |
| `romance-text-3.json`     | `/romance-text-3`     | Three layered marketing paragraphs              |
| `features.json`           | `/features`           | All product features (variable count)           |
| `features-10.json`        | `/features-10`        | Top 10 features ranked by consumer relevance    |
| `search-description.json` | `/search-description` | 150-160 char SEO-optimized description          |
| `attributes.json`         | `/attributes`         | Single attribute value with UOM conversion      |
| `everything.json`         | `/everything`         | Full bundle: romance, paragraphs, features, SEO |

**Config file structure:**

```json
{
    "name": "my-content-type",
    "description": "Human-readable description of what this produces.",
    "system_prompt_additions": "Additional instructions appended to the base Claude system prompt.",
    "output_schema": {
        "field_name": "type and description — Claude will populate this"
    }
}
```

---

## Request Pipeline

```
POST /features  [item1, item2, item3]
       │
       ▼
┌─────────────────────────────────────────┐
│  For each item (sequential):            │
│    Tavily.search_product(upc, brand, mfg) │
│    → raw Tavily JSON                    │
└─────────────────────────────────────────┘
       │  tavily_results = [r1, r2, r3]
       ▼
┌─────────────────────────────────────────┐
│  asyncio.gather (parallel):             │
│    Anthropic.extract(r1, config, item1) │
│    Anthropic.extract(r2, config, item2) │
│    Anthropic.extract(r3, config, item3) │
└─────────────────────────────────────────┘
       │
       ▼
  [result1, result2, result3]  →  HTTP 200
```

---

## Deployment to Databricks

### First-time setup

1. Create the app in Databricks (one time):

    ```bash
    databricks apps create ai-product-content-generator-api
    ```

2. Configure secrets in Databricks so the app can read `TAVILY_API_TOKEN` and `ANTHROPIC_API_TOKEN` at runtime (via Databricks Secrets or the App environment variable UI).

### Deploy

```powershell
.\deploy\deploy.ps1
```

This script:

1. Creates all required workspace directories under `/Workspace/ML_ai_squad/nrix/ai-product-content-generator-api/`.
2. Uploads all `.py`, `.yaml`, `.txt`, and `.json` files (excluding `.venv`, `__pycache__`, `.git`, `deploy/`).
3. Runs `databricks apps deploy` pointing to the uploaded source.

### Kill a stuck port (local dev)

```powershell
.\deploy\killports.ps1
```

---

## Adding a New Content Type

1. Add `json-structures/my-new-type.json` with `name`, `description`, `system_prompt_additions`, and `output_schema`.
2. Add a new endpoint in `routers/main.py` calling `_run_pipeline(items, "my-new-type")`.
3. No changes to `anthropic_service.py` or `tavily_service.py` are needed.
