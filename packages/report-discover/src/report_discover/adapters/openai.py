"""OpenAI adapter — a ready-made `LlmClient` with on-disk response caching.

Install with `pip install report-discover[openai]`.

This is a thin wrapper. Anything more elaborate (custom retry, alternative
SDKs, local models) can implement the `LlmClient` Protocol directly and
skip this module entirely.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar

import structlog
from pydantic import BaseModel

if TYPE_CHECKING:
    from diskcache import Cache
    from openai import OpenAI

log = structlog.get_logger()

T = TypeVar("T", bound=BaseModel)


def _cache_key(model: str, messages: list[dict[str, str]], schema_name: str) -> str:
    payload = json.dumps(
        {"model": model, "messages": messages, "schema": schema_name},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


class OpenAILlmClient:
    """OpenAI-backed `LlmClient` with optional disk cache.

    The cache is keyed on (model, messages, schema name) — identical inputs
    are returned without a network call. Set `cache_dir=None` to disable.
    """

    def __init__(self, api_key: str, cache_dir: Path | None = None) -> None:
        from openai import OpenAI

        self._client: OpenAI = OpenAI(api_key=api_key)
        self._cache: Cache | None = None
        if cache_dir is not None:
            from diskcache import Cache

            cache_dir.mkdir(parents=True, exist_ok=True)
            self._cache = Cache(str(cache_dir))

    def ping(self) -> bool:
        try:
            self._client.models.list()
            return True
        except Exception as e:
            log.warning("openai.ping_failed", err=str(e)[:120])
            return False

    def parse(
        self,
        *,
        model: str,
        system: str,
        user: str,
        schema: type[T],
    ) -> T:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        key = _cache_key(model, messages, schema.__name__)

        if self._cache is not None:
            cached = self._cache.get(key)
            if cached is not None:
                log.debug("openai.cache_hit", schema=schema.__name__, key=key[:12])
                return schema.model_validate_json(cached)

        log.info("openai.call", model=model, schema=schema.__name__, key=key[:12])
        response = self._client.beta.chat.completions.parse(
            model=model,
            messages=messages,  # type: ignore[arg-type]
            response_format=schema,
        )
        parsed = response.choices[0].message.parsed
        if parsed is None:
            raise RuntimeError(f"OpenAI returned no parsed content for schema {schema.__name__}")
        if self._cache is not None:
            self._cache.set(key, parsed.model_dump_json())
        return parsed

    def run_agent(
        self,
        *,
        model: str,
        system: str,
        user: str,
        tools: list[dict[str, Any]],
        executor: Callable[[str, dict[str, Any]], str],
        schema: type[T],
        max_iterations: int = 15,
    ) -> T:
        """Drive a tool-call loop until the model returns structured output.

        Implementation: the target `schema` is exposed to the model as
        a synthetic tool named `submit_<SchemaName>`. The agent calls
        regular tools (`fetch_url`, etc.) to navigate, then `submit_…`
        with its final structured payload. The first `submit_…` call
        terminates the loop and its arguments — already JSON-Schema-
        validated by OpenAI — become the parsed return value.

        This is more reliable than a separate "ask for structured
        output" call after the loop: the model never has to re-derive
        its answer from a free-form chat history, and it can't return
        a half-formed JSON blob at the end of a long context. If the
        loop hits `max_iterations` without a submit, we raise.

        Tool errors are caught and surfaced to the model as tool-result
        text — the agent gets a chance to correct itself rather than
        the whole call dying.
        """
        submit_name = f"submit_{schema.__name__.lstrip('_')}"
        submit_tool: dict[str, Any] = {
            "type": "function",
            "function": {
                "name": submit_name,
                "description": (
                    f"Submit your final answer as a `{schema.__name__}`. "
                    "Call this exactly once when you've gathered enough "
                    "information. After this, the loop ends."
                ),
                "parameters": schema.model_json_schema(),
            },
        }
        all_tools = [*tools, submit_tool]

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        log.info(
            "openai.agent_start",
            model=model,
            schema=schema.__name__,
            tools=[t["function"]["name"] for t in all_tools],
        )
        # Force the agent to submit in the final ~10% of the budget.
        # Without this, gpt-5 sometimes keeps fetching past the cap and
        # we lose the whole run. The threshold is tracked so we only
        # append the nudge user-message once.
        forced_submit_threshold = max_iterations - max(2, max_iterations // 10)
        force_submit_active = False
        for iteration in range(max_iterations):
            if iteration >= forced_submit_threshold and not force_submit_active:
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Budget almost exhausted. Submit your final "
                            f"`{submit_name}` now with whatever you have. "
                            "Skip any year you couldn't confirm."
                        ),
                    }
                )
                force_submit_active = True
            tool_choice: Any = (
                {"type": "function", "function": {"name": submit_name}}
                if force_submit_active
                else "required"
            )
            response = self._client.chat.completions.create(
                model=model,
                messages=messages,  # type: ignore[arg-type]
                tools=all_tools,  # type: ignore[arg-type]
                tool_choice=tool_choice,
            )
            msg = response.choices[0].message
            messages.append(msg.model_dump(exclude_none=True))

            # The SDK's `tool_calls` is a union of function-typed and
            # custom-typed calls; we only emit function tools, so
            # filter to those for type narrowing.
            tool_calls = [tc for tc in (msg.tool_calls or []) if tc.type == "function"]
            if not tool_calls:
                # Shouldn't happen with tool_choice="required"; bail.
                log.warning("openai.agent_no_tool_call", iter=iteration)
                break

            log.info(
                "openai.agent_iter",
                iter=iteration,
                tool_calls=[tc.function.name for tc in tool_calls],
            )

            # If the agent submitted, parse and return immediately.
            for tc in tool_calls:
                if tc.function.name == submit_name:
                    try:
                        parsed = schema.model_validate_json(tc.function.arguments or "{}")
                    except Exception as e:
                        # Surface to the model so it can retry with a fix
                        # instead of crashing the whole run.
                        log.warning("openai.agent_submit_invalid", err=str(e)[:200])
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": tc.id,
                                "content": (
                                    f"REJECTED: your `{submit_name}` payload "
                                    f"failed validation: {e}. Fix and resubmit."
                                ),
                            }
                        )
                        break
                    log.info("openai.agent_done", schema=schema.__name__)
                    return parsed

            # Run regular tool calls.
            for tc in tool_calls:
                if tc.function.name == submit_name:
                    continue  # already handled (or rejected) above
                try:
                    args = json.loads(tc.function.arguments or "{}")
                    result = executor(tc.function.name, args)
                except Exception as e:
                    result = f"ERROR: {e}"
                if len(result) > 30000:
                    result = result[:30000] + "\n…[truncated]"
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result,
                    }
                )

        log.warning("openai.agent_max_iterations", iterations=max_iterations)
        raise RuntimeError(
            f"Agent exhausted {max_iterations} iterations without calling "
            f"`{submit_name}` — increase `max_iterations` or simplify the task."
        )
