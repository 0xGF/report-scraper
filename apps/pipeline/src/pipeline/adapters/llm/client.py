"""Thin wrapper around OpenAI with a SHA-keyed disk cache.

Why wrap it:
- Cache every call so re-runs don't cost tokens or time.
- One place to swap models (classifier vs normalizer) + one place to mock.
- Pydantic-typed structured output — we never parse free-form LLM text.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TypeVar

import diskcache
import structlog
from openai import OpenAI
from pydantic import BaseModel

log = structlog.get_logger()

T = TypeVar("T", bound=BaseModel)


def _cache_key(model: str, messages: list[dict[str, str]], schema_name: str) -> str:
    payload = json.dumps(
        {"model": model, "messages": messages, "schema": schema_name},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


class LlmClient:
    def __init__(self, api_key: str, cache_dir: Path) -> None:
        self._client = OpenAI(api_key=api_key)
        cache_dir.mkdir(parents=True, exist_ok=True)
        self._cache = diskcache.Cache(str(cache_dir))

    def ping(self) -> bool:
        """Return True if the API key is live. Used to bail out before
        making expensive LLM-gated choices (hierarchy, extractor fallback).
        """
        try:
            self._client.models.list()
            return True
        except Exception as e:
            log.warning("llm.ping_failed", err=str(e)[:120])
            return False

    def parse(
        self,
        *,
        model: str,
        system: str,
        user: str,
        schema: type[T],
    ) -> T:
        """Call the model with a structured-output schema. Cached on-disk."""
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        key = _cache_key(model, messages, schema.__name__)

        cached = self._cache.get(key)
        if cached is not None:
            log.debug("llm.cache_hit", schema=schema.__name__, key=key[:12])
            return schema.model_validate_json(cached)

        log.info("llm.call", model=model, schema=schema.__name__, key=key[:12])
        response = self._client.beta.chat.completions.parse(
            model=model,
            messages=messages,  # type: ignore[arg-type]
            response_format=schema,
        )
        parsed = response.choices[0].message.parsed
        if parsed is None:
            raise RuntimeError(f"LLM returned no parsed content for schema {schema.__name__}")
        self._cache.set(key, parsed.model_dump_json())
        return parsed

    def parse_with_image(
        self,
        *,
        model: str,
        system: str,
        user: str,
        image_data_url: str,
        schema: type[T],
    ) -> T:
        """Vision call — pass a PNG/JPEG data URL alongside the prompt.

        Cache key includes a SHA hash of the image so identical inputs hit
        the cache; same on-disk store as text calls.
        """
        # Cache by the image's hash, not the full data URL — keeps the key
        # short while still being content-addressed.
        img_hash = hashlib.sha256(image_data_url.encode()).hexdigest()[:24]
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": f"{user}\n\n<image:{img_hash}>"},
        ]
        key = _cache_key(model, messages, schema.__name__)

        cached = self._cache.get(key)
        if cached is not None:
            log.debug("llm.vision_cache_hit", schema=schema.__name__, key=key[:12])
            return schema.model_validate_json(cached)

        log.info("llm.vision_call", model=model, schema=schema.__name__, key=key[:12])
        response = self._client.beta.chat.completions.parse(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user},
                        {"type": "image_url", "image_url": {"url": image_data_url}},
                    ],
                },
            ],
            response_format=schema,
        )
        parsed = response.choices[0].message.parsed
        if parsed is None:
            raise RuntimeError(f"LLM returned no parsed content for schema {schema.__name__}")
        self._cache.set(key, parsed.model_dump_json())
        return parsed
