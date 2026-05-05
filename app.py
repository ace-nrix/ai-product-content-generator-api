"""
AI Product Content Generator API
Author: Noah Rix
Description: FastAPI application that generates structured product content
             by combining Tavily search with Anthropic AI restructuring.
             Designed for deployment on Databricks Apps.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routers import main

app = FastAPI(
    title="AI Product Content Generator API",
    description=(
        "Generates structured product content (romance text, features, attributes, etc.) "
        "by querying Tavily for raw product data and restructuring it via Anthropic Claude."
    ),
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(main.router)


@app.get("/health", tags=["Health"])
async def health_check():
    """
    Author: Noah Rix
    Simple health check endpoint confirming the API is running.
    """
    return {"status": "healthy", "service": "ai-product-content-generator-api"}
