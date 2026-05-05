"""
Main Router
Author: Noah Rix
Description: Defines all product content generation endpoints. Each endpoint
             corresponds to a JSON structure config in json-structures/ and
             follows the same pipeline:
               1. Loop through input items, call Tavily for each (sequentially —
                  Tavily does not support concurrent batch requests).
               2. Collect all Tavily responses into a list.
               3. Dispatch all Anthropic extractions in parallel (asyncio.gather).
               4. Return the aggregated results array.

Attribute endpoints (/attributes) accept extended input items that include
attributeName, validValues, and inputUOM so Anthropic can match and convert
values to the correct unit before selecting from the valid values list.
"""

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from anthropic_service import AnthropicService
from tavily_service import TavilyService

router = APIRouter()

# ── Shared service instances ──────────────────────────────────────────────────
tavily = TavilyService()
anthropic_svc = AnthropicService()


# ── Request models ────────────────────────────────────────────────────────────

class ProductItem(BaseModel):
    """
    Author: Noah Rix
    Standard product identifier used by all non-attribute endpoints.
    At least one of upc or mfg should be provided for a meaningful Tavily query.
    """
    upc: str = Field(default="", description="Product UPC / EAN code")
    mfg: str = Field(default="", description="Manufacturer item / part number")
    brand: str = Field(default="", description="Brand or manufacturer name")


class AttributeRow(BaseModel):
    """
    Author: Noah Rix
    A single row from Selling_Attributes — one attribute to populate per row.
    """
    attribute_cd: str = Field(default="", description="Attribute_Cd from Selling_Attributes")
    attributeName: str = Field(description="Human-readable attribute name (e.g. 'Color Family')")
    validValues: list[str] = Field(description="Allowed Attribute_Value_Tx candidates")
    inputUOM: str = Field(default="", description="Unit_Of_Measure_Tx — empty if N/A")
    multi_value_fl: bool = Field(default=False, description="Multi_Value_Fl")


class AttributeItem(BaseModel):
    """
    Author: Noah Rix
    One product with all its Selling_Attributes rows bundled together.
    Maps to: one Ace_Article_Num with N Attribute_Cd rows from the DB.
    A single Tavily search is run per product; Anthropic populates every
    attribute row in a single pass.
    """
    upc: str = Field(default="", description="Product UPC / EAN code (Ace_Article_Num)")
    mfg: str = Field(default="", description="Manufacturer item / part number")
    brand: str = Field(default="", description="Brand or manufacturer name")
    attributes: list[AttributeRow] = Field(description="All attribute rows to populate for this product")


# ── Score annotation ──────────────────────────────────────────────────────────

def _annotate_tavily_score(
    result: dict[str, Any],
    tavily_result: dict,
    structure_config: dict,
) -> dict[str, Any]:
    """
    Author: Noah Rix
    Inject Tavily confidence scores into the result.

    - tavily_score (item level): the highest score from all Tavily results for
      this product — present on every response as a quick quality indicator.
    - Per-element scoring (list fields): fields listed in score_list_fields have
      each string element converted to {"text": <str>, "score": <float|null>}
      using the positional Tavily result score (result[0] → element[0], etc.).
      This gives each paragraph/item its own distinct confidence value based on
      which source most likely informed it. Falls back to null when there are
      more elements than Tavily results.
    """
    tavily_sources = tavily_result.get("results", [])
    scores = [r.get("score") for r in tavily_sources if r.get("score") is not None]

    # Item-level: max score across all sources
    result["tavily_score"] = max(scores) if scores else None

    # Per-element: positional score for designated list fields
    for field in structure_config.get("score_list_fields", []):
        field_value = result.get(field)
        if isinstance(field_value, list):
            result[field] = [
                {
                    "text": elem,
                    "score": tavily_sources[i].get("score") if i < len(tavily_sources) else None,
                }
                if isinstance(elem, str)
                else elem
                for i, elem in enumerate(field_value)
            ]

    return result


# ── Attributes pipeline ───────────────────────────────────────────────────────

async def _run_attributes_pipeline(
    items: list[AttributeItem],
) -> list[dict[str, Any]]:
    """
    Author: Noah Rix
    Dedicated pipeline for the /attributes endpoint.
    For each product item:
      1. Run one Tavily search (sequential).
      2. Send all attribute rows for that product to Anthropic in one call.
      3. Annotate with tavily_score and return.
    """
    if not items:
        return []

    results: list[dict[str, Any]] = []

    for item in items:
        item_dict = item.model_dump()

        # Step 1 — one Tavily search per product
        try:
            tavily_result = await tavily.search_product(
                upc=item_dict.get("upc", ""),
                brand=item_dict.get("brand", ""),
                mfg=item_dict.get("mfg", ""),
            )
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Tavily search failed for item {item_dict}: {exc}",
            ) from exc

        # Step 2 — Anthropic populates all attribute rows in one call
        try:
            extracted = await anthropic_svc.extract_attributes(
                tavily_result=tavily_result,
                item_context=item_dict,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Anthropic attribute extraction failed: {exc}",
            ) from exc

        # Step 3 — item-level tavily_score
        scores = [
            r.get("score")
            for r in tavily_result.get("results", [])
            if r.get("score") is not None
        ]
        extracted["tavily_score"] = max(scores) if scores else None
        results.append(extracted)

    return results


# ── Shared pipeline ───────────────────────────────────────────────────────────

async def _run_pipeline(
    items: list[dict],
    structure_name: str,
) -> list[dict[str, Any]]:
    """
    Author: Noah Rix
    Core pipeline shared by every endpoint:
      1. Query Tavily for each item sequentially (API limitation).
      2. Batch all Tavily responses to Anthropic concurrently.
      3. Return the final results list.

    Args:
        items:          List of item dicts (upc, mfg, brand, and optionally
                        attributeName, validValues, inputUOM).
        structure_name: JSON structure config name to load from json-structures/.

    Returns:
        List of Anthropic-structured dicts, one per input item.

    Raises:
        HTTPException 502: If Tavily or Anthropic calls fail.
    """
    if not items:
        return []

    # Step 1 — Tavily: sequential because Tavily does not support batch requests
    tavily_results: list[dict] = []
    for item in items:
        try:
            result = await tavily.search_product(
                upc=item.get("upc", ""),
                brand=item.get("brand", ""),
                mfg=item.get("mfg", ""),
            )
            tavily_results.append(result)
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Tavily search failed for item {item}: {exc}",
            ) from exc

    # Step 2 — Anthropic: parallel extraction across all items
    try:
        final_results = await anthropic_svc.batch_extract(
            items=items,
            tavily_results=tavily_results,
            structure_name=structure_name,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Anthropic extraction failed: {exc}",
        ) from exc

    # Step 3 — Annotate each result with Tavily confidence scores
    structure_config = anthropic_svc.load_structure_config(structure_name)
    final_results = [
        _annotate_tavily_score(result, tavily_results[i], structure_config)
        for i, result in enumerate(final_results)
    ]

    return final_results


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/romance-text", tags=["Product Content"])
async def generate_romance_text(items: list[ProductItem]) -> list[dict[str, Any]]:
    """
    Author: Noah Rix
    Generate a single 2-3 sentence marketing paragraph per product.

    Accepts a list of product identifiers. Each item is searched via Tavily
    then passed to Anthropic to produce romance_text suitable for e-commerce
    product listings.
    """
    return await _run_pipeline(
        items=[item.model_dump() for item in items],
        structure_name="romance-text",
    )


@router.post("/romance-text-3", tags=["Product Content"])
async def generate_romance_text_3(items: list[ProductItem]) -> list[dict[str, Any]]:
    """
    Author: Noah Rix
    Generate three layered marketing paragraphs per product:
    introduction, technical features, and target audience.

    Accepts a list of product identifiers. Tavily provides raw data;
    Anthropic structures it into exactly three distinct paragraphs.
    """
    return await _run_pipeline(
        items=[item.model_dump() for item in items],
        structure_name="romance-text-3",
    )


@router.post("/features", tags=["Product Content"])
async def generate_features(items: list[ProductItem]) -> list[dict[str, Any]]:
    """
    Author: Noah Rix
    Extract all identifiable product features from search results.

    Each feature is a concise phrase naming the feature and its consumer
    benefit. The number of features returned varies by how much data
    Tavily finds. No fixed limit.
    """
    return await _run_pipeline(
        items=[item.model_dump() for item in items],
        structure_name="features",
    )


@router.post("/features-10", tags=["Product Content"])
async def generate_features_10(items: list[ProductItem]) -> list[dict[str, Any]]:
    """
    Author: Noah Rix
    Generate exactly 10 product features ranked by consumer relevance.

    Useful for catalog systems that require a fixed feature count.
    Features are prioritized: performance specs first, then build quality,
    then convenience. Anthropic fills any gaps with general benefits
    without fabricating specific numbers.
    """
    return await _run_pipeline(
        items=[item.model_dump() for item in items],
        structure_name="features-10",
    )


@router.post("/search-description", tags=["Product Content"])
async def generate_search_description(items: list[ProductItem]) -> list[dict[str, Any]]:
    """
    Author: Noah Rix
    Generate an SEO-optimized search description (150-160 chars) per product.

    Suitable for meta descriptions, search result snippets, and catalog
    search indexes. Naturally integrates brand, product name, and key specs.
    """
    return await _run_pipeline(
        items=[item.model_dump() for item in items],
        structure_name="search-description",
    )


@router.post("/attributes", tags=["Product Attributes"])
async def generate_attributes(items: list[AttributeItem]) -> list[dict[str, Any]]:
    """
    Author: Noah Rix
    Populate all Selling_Attributes rows for each product in one request.

    Each item represents one product (Ace_Article_Num) with a list of
    attribute rows to populate. A single Tavily search is run per product
    and Anthropic fills every attribute in one pass, returning:
      - attribute_cd, attribute_name, selected_value, original_value_found,
        input_uom, confidence, conversion_notes
    """
    return await _run_attributes_pipeline(items=items)


@router.post("/everything", tags=["Product Content"])
async def generate_everything(items: list[ProductItem]) -> list[dict[str, Any]]:
    """
    Author: Noah Rix
    Generate all content types in a single request per product:
    romance text, three marketing paragraphs, full feature list,
    and SEO search description.

    Use this endpoint when you need the complete content bundle and want to
    minimize the number of API round-trips.
    """
    return await _run_pipeline(
        items=[item.model_dump() for item in items],
        structure_name="everything",
    )
