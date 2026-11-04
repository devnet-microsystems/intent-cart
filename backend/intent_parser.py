import json
import time
import random
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator

from .config import settings


class Mandate(BaseModel):
    model_config = {'extra':'allow'}
    query: str = Field(min_length=1)
    max_price: Optional[float] = None
    currency: str = "EUR"
    allowed_conditions: List[str] = Field(default_factory=list)
    forbidden_conditions: List[str] = Field(default_factory=list)
    min_specs: Dict[str, Any] = Field(default_factory=dict)
    size: Optional[str] = None
    material: Optional[str] = None
    material_percentage: Optional[int] = None
    clarification_required: Optional[Any] = None

    @field_validator("max_price", mode="before")
    @classmethod
    def _validate_price(cls, v):
        if isinstance(v, str):
            raise ValueError("max_price must be numeric, not currency text")
        return v


def parse_intent(text: str) -> dict:
    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY not configured")
    try:
        from google import genai
        from google.genai import types
    except Exception as exc:
        raise RuntimeError("google-genai not installed") from exc

    client = genai.Client(api_key=settings.gemini_api_key, http_options=types.HttpOptions(timeout=30000, retry_options=types.HttpRetryOptions(attempts=1)))
    prompt = (
        "Extract a purchasing mandate as JSON matching the provided schema. "
        "query must be a non-empty search string. max_price must be a number without currency symbols. "
        "currency must be an ISO code such as EUR. "
        "Do not invent missing requirements. If ambiguous, set clarification_required. "
        "User request: " + text
    )
    transient = {408, 429, 500, 502, 503, 504}
    last_exc = None
    deadline = time.time() + 90
    for attempt in range(5):
        if time.time() >= deadline:
            break
        try:
            resp = client.models.generate_content(
                model=settings.gemini_model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                ),
            )
            last_exc = None
            break
        except Exception as exc:
            status = getattr(exc, "code", None) or getattr(getattr(exc, "response", None), "status_code", None)
            if status in transient and attempt < 4 and time.time() < deadline:
                last_exc = exc
                remaining = max(0, deadline - time.time())
                delay = min(remaining, min(8, (2 ** attempt)) + random.random())
                time.sleep(delay)
                continue
            raise RuntimeError(f"Gemini request failed: {exc}") from exc
    if last_exc is not None:
        raise RuntimeError(f"Gemini request failed: {last_exc}") from last_exc

    try:
        data = resp.parsed
        if data is None:
            data = json.loads(resp.text)
        if isinstance(data, Mandate):
            data = data.model_dump()
        elif isinstance(data, dict):
            data = Mandate.model_validate(data).model_dump()
        else:
            raise ValueError("invalid structured response")
    except Exception as exc:
        raise RuntimeError(f"Invalid Gemini response: {exc}") from exc
    return data
