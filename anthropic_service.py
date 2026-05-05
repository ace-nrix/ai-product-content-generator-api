"""
Anthropic Service
Author: Noah Rix
Description: Wraps the Anthropic Claude API to restructure raw Tavily search
             results into typed product content JSON objects. The output schema
             and system prompt are driven by JSON structure config files stored
             in the json-structures/ directory, making it easy to add new content
             types without code changes.
"""

import json
import os
import re
from pathlib import Path
from typing import Any, Optional

import anthropic
from dotenv import load_dotenv

load_dotenv()

STRUCTURES_DIR = Path(__file__).parent / "json-structures"
CLAUDE_MODEL = "claude-sonnet-4-20250514"
MAX_TOKENS = 4096
TEMPERATURE = 0.1  # Low temperature for deterministic, structured output


class AnthropicService:
    """
    Author: Noah Rix
    Drives Anthropic Claude to extract structured product data from Tavily
    search results. Each content type (romance-text, features, attributes, etc.)
    is configured by a corresponding JSON file in json-structures/.
    """

    def __init__(self, api_key: Optional[str] = None):
        """
        Author: Noah Rix
        Initialize AnthropicService with the provided API key or fall back
        to the ANTHROPIC_API_TOKEN environment variable.
        """
        self.api_key = api_key or os.getenv("ANTHROPIC_API_TOKEN")
        if not self.api_key:
            raise ValueError(
                "Anthropic API key not found. Set ANTHROPIC_API_TOKEN in .env or pass api_key."
            )
        self.client = anthropic.AsyncAnthropic(api_key=self.api_key)

    # ──────────────────────────────────────────────────────────────────────
    # Structure config helpers
    # ──────────────────────────────────────────────────────────────────────

    def load_structure_config(self, structure_name: str) -> dict:
        """
        Author: Noah Rix
        Load a JSON structure config file from the json-structures/ directory.

        Args:
            structure_name: File stem, e.g. "romance-text" loads
                            json-structures/romance-text.json.

        Returns:
            Parsed config dict with keys: name, description,
            output_schema, system_prompt_additions.

        Raises:
            FileNotFoundError: If the config file does not exist.
        """
        config_path = STRUCTURES_DIR / f"{structure_name}.json"
        if not config_path.exists():
            raise FileNotFoundError(
                f"Structure config not found: {config_path}"
            )
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def build_system_prompt(self, structure_config: dict) -> str:
        """
        Author: Noah Rix
        Construct the Claude system prompt from a structure config.
        Combines a base product-extraction instruction set with the
        config's schema definition and any additional prompt directives.

        Args:
            structure_config: Loaded structure config dict.

        Returns:
            Full system prompt string for the Claude API call.
        """
        schema_str = json.dumps(structure_config.get("output_schema", {}), indent=2)
        additions = structure_config.get("system_prompt_additions", "")

        return (
            "You are a product data extractor for a retail catalog system.\n"
            "Your job is to extract and restructure product information from raw web search results.\n\n"
            "Rules:\n"
            "- Respond with ONLY valid JSON. No explanation, no markdown, no code fences.\n"
            "- Use null for any field you cannot confidently determine from the source text.\n"
            "- Use the exact schema below — do not add or remove fields.\n"
            "- Keep descriptions factual, engaging, and retailer-neutral.\n"
            f"{additions}\n\n"
            f"Required JSON schema:\n{schema_str}"
        )

    # ──────────────────────────────────────────────────────────────────────
    # Extraction
    # ──────────────────────────────────────────────────────────────────────

    async def extract(
        self,
        tavily_result: dict,
        structure_config: dict,
        item_context: dict,
    ) -> dict[str, Any]:
        """
        Author: Noah Rix
        Send a single Tavily result to Anthropic Claude for structured extraction.

        Args:
            tavily_result:    Raw Tavily response dict for one product.
            structure_config: Loaded structure config (schema + prompt).
            item_context:     Original request item dict with upc, brand, mfg,
                              and optionally attributeName, validValues, inputUOM.

        Returns:
            Parsed JSON dict matching the structure config's output_schema,
            or an error dict if parsing fails.
        """
        system_prompt = self.build_system_prompt(structure_config)

        # Build source text from Tavily results
        source_sections: list[str] = []
        for result in tavily_result.get("results", []):
            source_sections.append(
                f"Source URL: {result.get('url', 'N/A')}\n"
                f"Title: {result.get('title', 'N/A')}\n"
                f"Content: {result.get('content', '')}"
            )
        source_text = "\n\n---\n\n".join(source_sections)

        upc = item_context.get("upc", "")
        brand = item_context.get("brand", "")
        mfg = item_context.get("mfg", "")

        user_message = (
            f"Extract product data for the following item:\n"
            f"  UPC:   {upc}\n"
            f"  Brand: {brand}\n"
            f"  MFG#:  {mfg}\n\n"
            f"Search Results:\n{source_text}"
        )

        # Append attribute-specific instructions when present
        if "attributeName" in item_context:
            attr_name = item_context.get("attributeName", "")
            valid_values = item_context.get("validValues", [])
            input_uom = item_context.get("inputUOM", "")
            attribute_cd = item_context.get("attribute_cd", "")
            multi_value_fl = item_context.get("multi_value_fl", False)

            user_message += (
                f"\n\nAttribute to populate: {attr_name}\n"
                f"Attribute code (echo back as attribute_cd): {attribute_cd}\n"
                f"Required output UOM: {input_uom}\n"
                f"Multiple values allowed: {multi_value_fl}\n"
                f"You MUST select selected_value from this list (convert units if needed):\n"
                f"{json.dumps(valid_values, indent=2)}\n"
                f"If the found value uses different units than '{input_uom}', convert it "
                f"and match the converted value to the closest entry in the valid values list."
            )

        message = await self.client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=MAX_TOKENS,
            temperature=TEMPERATURE,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )

        response_text = "".join(
            block.text for block in message.content if hasattr(block, "text")
        )

        return self._parse_response(response_text)

    async def batch_extract(
        self,
        items: list[dict],
        tavily_results: list[dict],
        structure_name: str,
    ) -> list[dict[str, Any]]:
        """
        Author: Noah Rix
        Extract structured content for a batch of items concurrently.
        Tavily results are collected first (sequentially by the caller),
        then all Anthropic extractions are dispatched in parallel via asyncio.

        Args:
            items:           List of request item dicts (upc, brand, mfg, ...).
            tavily_results:  Corresponding list of raw Tavily responses, same order.
            structure_name:  Name of the JSON structure config to use.

        Returns:
            List of extracted content dicts, one per input item.
        """
        import asyncio

        structure_config = self.load_structure_config(structure_name)

        tasks = [
            self.extract(tavily_results[i], structure_config, items[i])
            for i in range(len(items))
        ]
        return list(await asyncio.gather(*tasks))

    async def extract_attributes(
        self,
        tavily_result: dict,
        item_context: dict,
    ) -> dict[str, Any]:
        """
        Author: Noah Rix
        Populate all attribute rows for a single product in one Anthropic call.
        Each row in item_context['attributes'] maps to a Selling_Attributes DB row.

        Returns a dict with upc, brand, model_number, product_name, and an
        'attributes' list where each entry has:
            attribute_cd, attribute_name, selected_value, original_value_found,
            input_uom, confidence, conversion_notes.
        """
        # Build source text from Tavily results
        source_sections: list[str] = []
        for result in tavily_result.get("results", []):
            source_sections.append(
                f"Source URL: {result.get('url', 'N/A')}\n"
                f"Title: {result.get('title', 'N/A')}\n"
                f"Content: {result.get('content', '')}"
            )
        source_text = "\n\n---\n\n".join(source_sections)

        upc = item_context.get("upc", "")
        brand = item_context.get("brand", "")
        mfg = item_context.get("mfg", "")
        attribute_rows = item_context.get("attributes", [])

        # Build the attributes task list for the prompt
        rows_text = json.dumps(attribute_rows, indent=2)

        system_prompt = (
            "You are a product data extractor for a retail catalog system.\n"
            "You will receive a list of attribute rows to populate for a single product.\n"
            "For each row, find the attribute value in the search results and match it to "
            "the closest entry in that row's validValues list.\n\n"
            "Rules:\n"
            "- Respond with ONLY valid JSON. No explanation, no markdown, no code fences.\n"
            "- Return a JSON object with: upc, brand, model_number, product_name, and an "
            "'attributes' array — one entry per input attribute row, in the same order.\n"
            "- Each attributes entry must have: attribute_cd, attribute_name, selected_value, "
            "original_value_found, input_uom, confidence (high/medium/low), conversion_notes.\n"
            "- selected_value MUST be from the row's validValues list or null.\n"
            "- If inputUOM is provided and the found value is in different units, convert before matching.\n"
            "- Use null for any field you cannot confidently determine."
        )

        user_message = (
            f"Product:\n"
            f"  UPC:   {upc}\n"
            f"  Brand: {brand}\n"
            f"  MFG#:  {mfg}\n\n"
            f"Attribute rows to populate:\n{rows_text}\n\n"
            f"Search Results:\n{source_text}"
        )

        message = await self.client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=MAX_TOKENS,
            temperature=TEMPERATURE,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )

        response_text = "".join(
            block.text for block in message.content if hasattr(block, "text")
        )

        return self._parse_response(response_text)

    # ──────────────────────────────────────────────────────────────────────
    # Response parsing
    # ──────────────────────────────────────────────────────────────────────

    def _parse_response(self, response_text: str) -> dict[str, Any]:
        """
        Author: Noah Rix
        Attempt to parse the Claude response as JSON. Falls back to a regex
        extraction if the response contains surrounding text.

        Args:
            response_text: Raw string returned by Claude.

        Returns:
            Parsed dict, or an error dict with the raw response.
        """
        text = response_text.strip()

        # Direct JSON parse
        if text.startswith("{") or text.startswith("["):
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                pass

        # Regex fallback — extract first {...} block
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass

        return {"error": "Failed to parse Anthropic response as JSON", "raw": text}
