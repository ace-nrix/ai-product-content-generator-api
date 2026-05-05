"""
Tavily Service
Author: Noah Rix
Description: Wrapper for the Tavily Search API. Handles all outbound search
             requests for product lookups by UPC, manufacturer number, and brand.
             All requests are structured and filtered to exclude acehardware.com.
"""

import os
import re
from html import unescape
from typing import Optional

import httpx
from dotenv import load_dotenv

load_dotenv()

TAVILY_API_URL = "https://api.tavily.com/search"
DEFAULT_EXCLUDE_DOMAINS = ["www.acehardware.com"]


class TavilyService:
    """
    Author: Noah Rix
    Wraps the Tavily REST API to provide product search functionality.
    Tavily does not support concurrent batch requests; callers must
    loop through items and invoke search_product for each one individually.
    """

    def __init__(self, api_key: Optional[str] = None):
        """
        Author: Noah Rix
        Initialize TavilyService with the provided API key or fall back
        to the TAVILY_API_TOKEN environment variable.
        """
        self.api_key = api_key or os.getenv("TAVILY_API_TOKEN")
        if not self.api_key:
            raise ValueError(
                "Tavily API key not found. Set TAVILY_API_TOKEN in .env or pass api_key."
            )

    async def search(
        self,
        query: str,
        max_results: int = 3,
        exclude_domains: Optional[list] = None,
    ) -> dict:
        """
        Author: Noah Rix
        Send a raw search request to the Tavily API.

        Args:
            query:          The search query string.
            max_results:    Maximum number of results to return (default 3).
            exclude_domains: Domains to exclude from results.

        Returns:
            Raw Tavily response dict containing query, results, response_time, etc.
        """
        payload = {
            "api_key": self.api_key,
            "query": query,
            "max_results": max_results,
            "include_images": False,
            "exclude_domains": exclude_domains or DEFAULT_EXCLUDE_DOMAINS,
            "search_depth": "advanced",
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(TAVILY_API_URL, json=payload)
            response.raise_for_status()
            return response.json()

    async def search_product(
        self,
        upc: str = "",
        brand: str = "",
        mfg: str = "",
    ) -> dict:
        """
        Author: Noah Rix
        Build a product-focused query from UPC, brand, and/or manufacturer number
        and send it to Tavily. At least one identifier should be provided.

        Args:
            upc:   Product UPC/EAN code.
            brand: Brand or manufacturer name.
            mfg:   Manufacturer item/part number.

        Returns:
            Raw Tavily response dict.
        """
        query_parts: list[str] = []

        if upc:
            query_parts.append(f"UPC {upc}")
        if brand:
            query_parts.append(brand)
        if mfg:
            query_parts.append(f'"{mfg}"')

        query_parts.append("product specifications details features")
        query = " ".join(query_parts)

        return await self.search(query)

    # ──────────────────────────────────────────────────────────────────────
    # Content cleaning helpers (ported from ace-product-content-generator)
    # ──────────────────────────────────────────────────────────────────────

    def clean_content(self, content: str) -> str:
        """
        Author: Noah Rix
        Strip HTML, boilerplate navigation copy, price references, and noise
        from a raw Tavily result content string.

        Args:
            content: Raw string from a Tavily result's 'content' field.

        Returns:
            Cleaned string suitable for feeding to Anthropic.
        """
        if not content:
            return ""

        content = unescape(content)
        content = re.sub(r"<[^>]+>", " ", content)

        boilerplate_patterns = [
            r"(?i)cookie.*?policy",
            r"(?i)privacy.*?policy",
            r"(?i)terms.*?of.*?service",
            r"(?i)sign.*?up.*?for.*?newsletter",
            r"(?i)subscribe.*?to.*?our",
            r"(?i)add.*?to.*?cart",
            r"(?i)buy.*?now",
            r"(?i)related.*?products",
            r"(?i)you.*?may.*?also.*?like",
            r"(?i)customer.*?reviews?",
            r"(?i)free.*?shipping",
            r"(?i)store.*?locator",
            r"(?i)advertisement",
            r"(?i)sponsored",
            r"(?i)javascript.*?required",
            r"(?i)this.*?site.*?uses.*?cookies",
        ]
        for pattern in boilerplate_patterns:
            content = re.sub(pattern, " ", content)

        # Remove price references
        content = re.sub(r"\$[\d,]+\.?\d*", "", content)

        content = re.sub(r"\s+", " ", content).strip()
        return content

    def extract_product_info(self, title: str, content: str) -> str:
        """
        Author: Noah Rix
        Combine title and cleaned content into a formatted product info string,
        retaining only sentences with product-relevant keywords.

        Args:
            title:   Page title from Tavily result.
            content: Raw content from Tavily result.

        Returns:
            Formatted, filtered string with source label prepended.
        """
        cleaned = self.clean_content(content)

        product_keywords = {
            "product", "model", "brand", "feature", "specification",
            "dimension", "material", "color", "size", "weight", "capacity",
            "power", "voltage", "inch", "foot", "pound", "gallon", "watt",
            "amp", "rated", "certified", "includes", "compatible",
            "designed", "manufactured", "upc", "ean", "item number",
            "model number", "part number", "mpn",
        }

        sentences = cleaned.split(".")
        kept = [
            s.strip()
            for s in sentences
            if len(s.strip()) > 20
            and any(kw in s.lower() for kw in product_keywords)
        ]

        if not kept:
            return ""

        body = ". ".join(kept)
        if not body.endswith("."):
            body += "."

        return f"[{title}] {body}"
