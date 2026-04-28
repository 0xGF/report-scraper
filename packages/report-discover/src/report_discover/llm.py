"""LLM client Protocols — BYO model.

The library never imports a specific SDK. Anything that satisfies these
interfaces works.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LlmClient(Protocol):
    """Minimal contract for a structured-output LLM call.

    Implementations must return a parsed instance of the given Pydantic
    schema. Caching, retries, and provider choice are the implementation's
    business — the library only cares that calls succeed and conform.
    """

    def parse(
        self,
        *,
        model: str,
        system: str,
        user: str,
        schema: type[T],
    ) -> T:
        """Run a single structured-output call and return the parsed result."""
        ...

    def ping(self) -> bool:
        """Cheap reachability check. Return True iff the LLM API is live.

        Used to bail out early on dead keys instead of failing mid-pipeline.
        """
        ...


class AgentLlmClient(LlmClient, Protocol):
    """Tool-using LLM Protocol — used by the agent-based discoverer.

    Extends `LlmClient` with a tool-call loop. Any client that can
    drive a tool-call loop satisfies this; the library invokes
    `run_agent` with tool descriptions, an executor that resolves
    tool calls to string responses, and a target schema, and the
    implementation runs the loop and returns the parsed final result.

    A single client can satisfy both Protocols (the bundled
    `OpenAILlmClient` does); pass it as either type depending on
    which surface a given call site needs.
    """

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
        """Run a tool-using loop until the model returns a parsed `schema`.

        `tools` follows the OpenAI tool-spec shape:
          `[{"type": "function", "function": {"name": ..., "description": ...,
            "parameters": {<JSON Schema>}}}, ...]`

        `executor(name, args)` runs a tool call and returns its string
        result. Implementations are expected to surface tool errors
        back to the model as a tool-result message rather than raising.

        `max_iterations` bounds the loop to keep token spend predictable.
        """
        ...
