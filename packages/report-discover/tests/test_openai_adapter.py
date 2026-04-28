"""Tests for `OpenAILlmClient.run_agent` — the tool-using loop driver.

The loop is the most complex code path in the package: synthetic
submit-tool, tool-choice forcing, structured-output validation. We
test it with a mocked OpenAI client so the test stays offline and
deterministic. Each scenario stages the sequence of messages the
mock will return; the loop drives them.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from pydantic import BaseModel

from report_discover.adapters.openai import OpenAILlmClient


class _Result(BaseModel):
    answer: str


def _tool_call(call_id: str, name: str, args: str) -> SimpleNamespace:
    """Stand-in for the OpenAI SDK's tool-call object."""
    return SimpleNamespace(
        id=call_id,
        type="function",
        function=SimpleNamespace(name=name, arguments=args),
    )


def _msg(content: str | None = None, tool_calls: list[Any] | None = None) -> SimpleNamespace:
    """Stand-in for the SDK's `ChatCompletionMessage`."""
    msg = SimpleNamespace(
        content=content,
        tool_calls=tool_calls,
        role="assistant",
    )
    msg.model_dump = lambda **_kw: {
        "role": "assistant",
        "content": content,
        "tool_calls": tool_calls,
    }
    return msg


def _make_client(messages_in_order: list[SimpleNamespace]) -> tuple[OpenAILlmClient, MagicMock]:
    """Build an `OpenAILlmClient` whose underlying SDK is a MagicMock
    that returns the given assistant messages in sequence."""
    client = OpenAILlmClient.__new__(
        OpenAILlmClient
    )  # bypass __init__ (which builds a real OpenAI client)
    sdk = MagicMock()
    responses = [SimpleNamespace(choices=[SimpleNamespace(message=m)]) for m in messages_in_order]
    sdk.chat.completions.create.side_effect = responses
    client._client = sdk
    client._cache = None
    return client, sdk


def _no_op_executor(_name: str, _args: dict[str, Any]) -> str:
    return "(unused)"


# --------------------------------------------------------------------------- #
# `run_agent` — happy path: submit on the first iteration
# --------------------------------------------------------------------------- #


def test_run_agent_returns_on_first_submit() -> None:
    """Agent calls the synthetic submit tool → loop terminates and the
    parsed schema instance is returned."""
    submit = _tool_call("call_1", "submit_Result", '{"answer": "42"}')
    client, _sdk = _make_client([_msg(tool_calls=[submit])])

    result = client.run_agent(
        model="gpt-4o",
        system="sys",
        user="usr",
        tools=[],
        executor=_no_op_executor,
        schema=_Result,
        max_iterations=5,
    )
    assert isinstance(result, _Result)
    assert result.answer == "42"


# --------------------------------------------------------------------------- #
# `run_agent` — runs a regular tool, then submits
# --------------------------------------------------------------------------- #


def test_run_agent_dispatches_regular_tool_then_submits() -> None:
    """Agent calls a regular tool first; its result is appended as a
    `tool` message; agent submits next iteration."""
    fetch = _tool_call("call_1", "fetch_url", '{"url": "https://x.com"}')
    submit = _tool_call("call_2", "submit_Result", '{"answer": "ok"}')
    client, sdk = _make_client([_msg(tool_calls=[fetch]), _msg(tool_calls=[submit])])

    seen_calls: list[tuple[str, dict[str, Any]]] = []

    def executor(name: str, args: dict[str, Any]) -> str:
        seen_calls.append((name, args))
        return "fake result"

    result = client.run_agent(
        model="gpt-4o",
        system="sys",
        user="usr",
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "fetch_url",
                    "description": "fetch",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
        executor=executor,
        schema=_Result,
        max_iterations=5,
    )
    assert result.answer == "ok"
    assert seen_calls == [("fetch_url", {"url": "https://x.com"})]
    # 2 LLM calls: regular tool turn + submit turn.
    assert sdk.chat.completions.create.call_count == 2


# --------------------------------------------------------------------------- #
# `run_agent` — invalid submit: rejected, agent gets a chance to retry
# --------------------------------------------------------------------------- #


def test_run_agent_rejects_invalid_submit_then_accepts_retry() -> None:
    """An invalid submit payload is surfaced to the model as a tool
    error message; the agent gets a chance to fix and resubmit."""
    bad = _tool_call("call_1", "submit_Result", '{"wrong_field": "x"}')
    good = _tool_call("call_2", "submit_Result", '{"answer": "fixed"}')
    client, _sdk = _make_client([_msg(tool_calls=[bad]), _msg(tool_calls=[good])])

    result = client.run_agent(
        model="gpt-4o",
        system="sys",
        user="usr",
        tools=[],
        executor=_no_op_executor,
        schema=_Result,
        max_iterations=5,
    )
    assert result.answer == "fixed"


# --------------------------------------------------------------------------- #
# `run_agent` — budget exhausted
# --------------------------------------------------------------------------- #


def test_run_agent_raises_when_budget_exhausted_without_submit() -> None:
    """If the agent exhausts max_iterations without calling submit
    (even after the forced-submit nudge), we raise rather than return
    a partial / fabricated result."""
    fetch = _tool_call("call", "fetch_url", "{}")
    # 5 turns, all of which call fetch_url instead of submit. Force
    # submit kicks in at threshold = 5 - max(2, 0) = 3.
    msgs = [_msg(tool_calls=[fetch]) for _ in range(5)]
    client, _sdk = _make_client(msgs)

    try:
        client.run_agent(
            model="gpt-4o",
            system="sys",
            user="usr",
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "fetch_url",
                        "description": "fetch",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ],
            executor=_no_op_executor,
            schema=_Result,
            max_iterations=5,
        )
    except RuntimeError as e:
        assert "submit_Result" in str(e)
    else:
        raise AssertionError("expected RuntimeError on budget exhaustion")


# --------------------------------------------------------------------------- #
# `run_agent` — tool-choice forces submit when the budget runs low
# --------------------------------------------------------------------------- #


def test_run_agent_forces_submit_at_budget_threshold() -> None:
    """In the final ~10% of the budget, `tool_choice` is set to force
    the synthetic submit tool. We assert this by inspecting the calls
    the SDK received."""
    submit = _tool_call("call", "submit_Result", '{"answer": "x"}')
    # Loop will call create() repeatedly; we only need one response,
    # but we structure the call so the submit happens late.
    client, sdk = _make_client([_msg(tool_calls=[submit])])

    client.run_agent(
        model="gpt-4o",
        system="sys",
        user="usr",
        tools=[],
        executor=_no_op_executor,
        schema=_Result,
        max_iterations=2,  # threshold = 2 - max(2, 0) = 0 → force from iter 0
    )

    # First (only) SDK call should have the forced tool_choice.
    call_kwargs = sdk.chat.completions.create.call_args_list[0].kwargs
    forced = call_kwargs["tool_choice"]
    assert isinstance(forced, dict)
    assert forced["function"]["name"] == "submit_Result"


# --------------------------------------------------------------------------- #
# `run_agent` — executor exceptions surface to the model as tool-result text
# --------------------------------------------------------------------------- #


def test_run_agent_surfaces_executor_exceptions() -> None:
    """An exception from the executor is caught and converted to a
    tool-result `ERROR: …` message — the loop continues so the agent
    can recover, rather than dying mid-task."""
    fetch_bad = _tool_call("c1", "fetch_url", "{}")
    submit = _tool_call("c2", "submit_Result", '{"answer": "after-error"}')
    client, _sdk = _make_client([_msg(tool_calls=[fetch_bad]), _msg(tool_calls=[submit])])

    def executor(_name: str, _args: dict[str, Any]) -> str:
        raise RuntimeError("simulated tool failure")

    result = client.run_agent(
        model="gpt-4o",
        system="sys",
        user="usr",
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "fetch_url",
                    "description": "fetch",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
        executor=executor,
        schema=_Result,
        max_iterations=5,
    )
    assert result.answer == "after-error"
