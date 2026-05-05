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


class AttributeItem(BaseModel):
    """
    Author: Noah Rix
    Extended product item used by attribute endpoints. Includes the attribute
    name to populate, a list of valid values to choose from, and the required
    output unit of measure. Anthropic will auto-convert to inputUOM if the
    found value is in a different unit.
    """
    upc: str = Field(default="", description="Product UPC / EAN code")
    mfg: str = Field(default="", description="Manufacturer item / part number")
    brand: str = Field(default="", description="Brand or manufacturer name")
    inputUOM: str = Field(description="Required output unit of measure (e.g. 'inches', 'lbs')")
    attributeName: str = Field(description="Name of the attribute to populate (e.g. 'Width')")
    validValues: list[str] = Field(description="Allowed values Anthropic must select from")


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
    Populate a specific named attribute for each product, selecting from a
    provided list of valid values. Supports automatic UOM conversion.

    Each item must include:
      - attributeName: the attribute to find (e.g. "Width")
      - validValues:   list of allowed selections (e.g. ["6 in", "8 in", "12 in"])
      - inputUOM:      required output unit (e.g. "inches")

    Anthropic converts the found value to inputUOM before matching it to the
    validValues list, enabling on-the-fly unit conversions (feet → inches, etc.).
    """
    return await _run_pipeline(
        items=[item.model_dump() for item in items],
        structure_name="attributes",
    )


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
