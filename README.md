# AI Product Content Generator API

> **Author:** Noah Rix  
> **Repo:** https://github.com/ace-nrix/ai-product-content-generator-api  
> **Deployment target:** Databricks Apps  
> **Live URL:** https://ai-pcg-api-439895488707306.6.azure.databricksapps.com

A FastAPI service that generates structured product content for retail catalogs — designed as a plug-and-play AI backend for the **New Item Onboarding (NIO)** application. Given a UPC, manufacturer number, and/or brand name, it:

1. **Searches Tavily** for raw product specifications and marketing copy from across the web (excluding acehardware.com).
2. **Passes the results to Anthropic Claude** which restructures the raw search data into typed JSON content objects driven by swappable config files.
3. **Annotates every response** with Tavily confidence scores so the caller can gauge data quality and decide whether to auto-accept or flag for human review.

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

Create a `.env` file:

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

### Standard endpoints

`POST /romance-text` · `/romance-text-3` · `/features` · `/features-10` · `/search-description` · `/everything`

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

---

### Attribute endpoint — `POST /attributes`

Designed to map directly to the `Ace_PCM_Golden_Copy.dbo.Selling_Attributes` table. One request item = one product (`Ace_Article_Num`) with **all its attribute rows bundled together**. A single Tavily search is run per product; Anthropic populates every attribute in one pass.

**Request body:**

```json
[
    {
        "upc": "1009203",
        "brand": "Benjamin Moore",
        "attributes": [
            {
                "attribute_cd": "64878",
                "attributeName": "Brand Name",
                "validValues": ["Benjamin Moore", "Behr", "Sherwin-Williams"],
                "inputUOM": "",
                "multi_value_fl": false
            },
            {
                "attribute_cd": "64888",
                "attributeName": "Color Collection",
                "validValues": ["Classic Colors", "Aura", "Regal"],
                "inputUOM": "",
                "multi_value_fl": false
            },
            {
                "attribute_cd": "64891",
                "attributeName": "Color Family",
                "validValues": ["Green", "Blue", "Red", "Neutral"],
                "inputUOM": "",
                "multi_value_fl": false
            }
        ]
    }
]
```

**`AttributeItem` fields:**

| Field        | Type                  | DB Column         | Description                     |
| ------------ | --------------------- | ----------------- | ------------------------------- |
| `upc`        | string                | `Ace_Article_Num` | Product UPC / EAN code          |
| `mfg`        | string                | —                 | Manufacturer item / part number |
| `brand`      | string                | —                 | Brand name                      |
| `attributes` | `array<AttributeRow>` | —                 | All attribute rows to populate  |

**`AttributeRow` fields:**

| Field            | Type            | DB Column            | Description                              |
| ---------------- | --------------- | -------------------- | ---------------------------------------- |
| `attribute_cd`   | string          | `Attribute_Cd`       | Attribute code (echoed back in response) |
| `attributeName`  | string          | —                    | Human-readable attribute name            |
| `validValues`    | `array<string>` | `Attribute_Value_Tx` | Allowed values Claude must select from   |
| `inputUOM`       | string          | `Unit_Of_Measure_Tx` | Required output unit — empty if N/A      |
| `multi_value_fl` | boolean         | `Multi_Value_Fl`     | Whether multiple values are allowed      |

**Response:**

```json
[
    {
        "upc": "1009203",
        "brand": "Benjamin Moore",
        "product_name": "Benjamin Moore High Park Paint",
        "tavily_score": 0.921,
        "attributes": [
            {
                "attribute_cd": "64878",
                "attribute_name": "Brand Name",
                "selected_value": "Benjamin Moore",
                "original_value_found": "Benjamin Moore",
                "input_uom": "",
                "confidence": "high",
                "conversion_notes": null
            }
        ]
    }
]
```

---

## Tavily Confidence Scores

Every response includes confidence scores sourced directly from Tavily's search result `score` field (a float between 0 and 1, where higher = more relevant).

### `tavily_score` (item level)

Present on **every endpoint response**. The highest score across all Tavily sources fetched for that product — a quick overall quality indicator for the generated content.

```json
{ "product_name": "...", "tavily_score": 0.9635 }
```

### `relevance` (per-element)

On endpoints that return arrays of generated content (`/features-10`, `/romance-text-3`, `romance_text_paragraphs` in `/everything`), each element carries a `relevance` field.

**How it's calculated:** Tavily returns its sources pre-sorted by relevance descending. Each generated element is paired with the Tavily source at the same position — element 0 gets `results[0].score`, element 1 gets `results[1].score`, and so on. This is a _positional proxy_: it reflects how relevant Tavily considered the source most likely informing that element, not a confidence score computed by Claude. Elements beyond the number of available sources fall back to the top score.

In practice for `/features-10`: Claude reads all 10 sources simultaneously and ranks features by consumer importance — so feature 4 may actually draw from source 1. The `relevance` value still tells you how far down the search result list was needed to cover that positional slot, which serves as a useful data-freshness/source-quality signal for NIO to use as a threshold.

```json
{
    "features": [
        { "text": "16GB GDDR6 memory — high-res gaming",    "relevance": 0.8679 },
        { "text": "4352 CUDA cores — parallel processing",  "relevance": 0.8663 },
        { "text": "2535 MHz boost clock — max performance", "relevance": 0.8522 },
        { "text": "DLSS 3 — AI-accelerated frame rates",    "relevance": 0.8614 },
        ...
    ],
    "tavily_score": 0.8679
}
```

Each structure config controls how many Tavily sources are fetched via `tavily_max_results`. `features-10` requests 10 sources to give each feature its own distinct relevance value; other endpoints default to 3.

---

## Content Type Configs (`json-structures/`)

Each JSON file drives one endpoint. Editing a file immediately changes the output schema, Anthropic prompt, and Tavily fetch size — no code change required.

| File                      | Endpoint              | Output                                          | Tavily Sources |
| ------------------------- | --------------------- | ----------------------------------------------- | -------------- |
| `romance-text.json`       | `/romance-text`       | Single 2-3 sentence marketing paragraph         | 3              |
| `romance-text-3.json`     | `/romance-text-3`     | Three layered marketing paragraphs (per-score)  | 3              |
| `features.json`           | `/features`           | All product features (variable count)           | 3              |
| `features-10.json`        | `/features-10`        | Top 10 features ranked by consumer relevance    | 10             |
| `search-description.json` | `/search-description` | 150-160 char SEO-optimized description          | 3              |
| `attributes.json`         | `/attributes`         | All attribute rows for a product in one pass    | 3              |
| `everything.json`         | `/everything`         | Full bundle: romance, paragraphs, features, SEO | 3              |

**Config file keys:**

```json
{
    "name": "my-content-type",
    "description": "Human-readable description.",
    "system_prompt_additions": "Extra instructions appended to the base Claude system prompt.",
    "tavily_max_results": 3,
    "score_list_fields": ["field_name"],
    "output_schema": {
        "field_name": "type and description — Claude will populate this"
    }
}
```

| Key                       | Required | Description                                                                |
| ------------------------- | -------- | -------------------------------------------------------------------------- |
| `name`                    | yes      | Must match the filename stem                                               |
| `description`             | yes      | Shown in docs                                                              |
| `system_prompt_additions` | yes      | Appended to base Claude system prompt                                      |
| `output_schema`           | yes      | JSON schema Claude must follow                                             |
| `tavily_max_results`      | no       | How many Tavily sources to fetch (default: 3)                              |
| `score_list_fields`       | no       | Array field names whose string elements get per-element `relevance` scores |

---

## Request Pipeline

```
POST /features-10  [item1, item2]
       │
       ▼
┌─────────────────────────────────────────────┐
│  Load structure config (tavily_max_results) │
└─────────────────────────────────────────────┘
       │
       ▼
┌─────────────────────────────────────────────┐
│  For each item (sequential):                │
│    Tavily.search_product(upc, brand, mfg,   │
│                          max_results=10)    │
│    → 10 ranked sources with scores          │
└─────────────────────────────────────────────┘
       │  tavily_results = [r1, r2]
       ▼
┌─────────────────────────────────────────────┐
│  asyncio.gather (parallel):                 │
│    Anthropic.extract(r1, config, item1)     │
│    Anthropic.extract(r2, config, item2)     │
└─────────────────────────────────────────────┘
       │
       ▼
┌─────────────────────────────────────────────┐
│  Annotate scores:                           │
│    result.tavily_score = max(source scores) │
│    result.features[i].score = sources[i]   │
└─────────────────────────────────────────────┘
       │
       ▼
  [result1, result2]  →  HTTP 200
```

---

## Deployment to Databricks

### First-time setup

1. Install Databricks CLI and authenticate:

    ```bash
    databricks auth login
    ```

2. Create the app (one time):

    ```bash
    databricks apps create ai-pcg-api --description "AI Product Content Generator API"
    ```

### Deploy

```powershell
.\deploy\deploy.ps1
```

This script:

1. Creates all required workspace directories under `/Workspace/ML_ai_squad/nrix/ai-pcg-api/`.
2. Uploads all `.py`, `.yaml`, `.txt`, `.json`, and `.env` files (excluding `.venv`, `__pycache__`, `.git`, `deploy/`).
3. Runs `databricks apps deploy` pointing to the uploaded source.

### View logs

```bash
databricks apps logs ai-pcg-api --tail-lines 100 --profile nrix
```

### Kill a stuck port (local dev)

```powershell
.\deploy\killports.ps1
```

---

## Adding a New Content Type

1. Add `json-structures/my-new-type.json` with `name`, `description`, `system_prompt_additions`, `output_schema`, and optionally `tavily_max_results` / `score_list_fields`.
2. Add a new endpoint in `routers/main.py` calling `_run_pipeline(items, "my-new-type")`.
3. No changes to `anthropic_service.py` or `tavily_service.py` are needed.

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
