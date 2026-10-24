from __future__ import annotations

from hexgate.config import OptimizerConfig
from hexgate.optimizer import TRUNCATION_MARKER, optimize


def config(**overrides) -> OptimizerConfig:
    base = OptimizerConfig()
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


def test_disabled_returns_request_untouched():
    body = {"messages": [{"role": "user", "content": "  hi  "}]}
    out, report = optimize(body, config(enabled=False))
    assert out is body
    assert report.chars_before == report.chars_after
    assert report.tokens_saved == 0
    assert report.actions == []


def test_trims_trailing_whitespace_per_line():
    body = {"messages": [{"role": "user", "content": "one   \ntwo\t\nthree"}]}
    out, report = optimize(body, config())
    assert out["messages"][0]["content"] == "one\ntwo\nthree"
    assert report.chars_saved > 0
    assert body["messages"][0]["content"] == "one   \ntwo\t\nthree"


def test_strips_empty_messages_but_keeps_assistant_turns():
    body = {
        "messages": [
            {"role": "user", "content": "   "},
            {"role": "assistant", "content": None},
            {"role": "user", "content": "real"},
            {"role": "tool", "tool_call_id": "c", "content": None},
        ]
    }
    out, _ = optimize(body, config())
    roles = [m["role"] for m in out["messages"]]
    assert roles == ["assistant", "user"]


def test_merges_duplicate_system_prompts():
    body = {
        "messages": [
            {"role": "system", "content": "alpha\n\nbeta"},
            {"role": "system", "content": "beta\n\ngamma"},
            {"role": "user", "content": "go"},
        ]
    }
    out, report = optimize(body, config())
    assert len(out["messages"]) == 2
    assert out["messages"][0] == {"role": "system", "content": "alpha\n\nbeta\n\ngamma"}
    assert out["messages"][1] == {"role": "user", "content": "go"}
    rules = {action["rule"]: action["chars"] for action in report.actions}
    assert rules["dedupe-system"] > 0


def test_single_system_prompt_is_left_alone():
    body = {
        "messages": [
            {"role": "system", "content": "only one"},
            {"role": "user", "content": "go"},
        ]
    }
    out, report = optimize(body, config())
    assert out["messages"][0]["content"] == "only one"
    assert "dedupe-system" not in {action["rule"] for action in report.actions}


def test_truncates_long_tool_results_with_marker():
    long_text = "a" * 500 + "z" * 499
    body = {
        "messages": [
            {"role": "user", "content": "read it"},
            {"role": "tool", "tool_call_id": "c1", "content": long_text},
        ]
    }
    out, report = optimize(body, config(max_tool_result_chars=200))
    content = out["messages"][1]["content"]

    assert content.startswith("a" * 140)
    assert TRUNCATION_MARKER.format(removed=len(long_text) - 200) in content
    assert content.endswith("z" * 60)
    assert len(content) < len(long_text)

    rules = {action["rule"]: action["chars"] for action in report.actions}
    assert rules["truncate-tool-output"] == len(long_text) - 200
    assert report.tokens_saved > 0


def test_short_tool_results_are_untouched():
    body = {"messages": [{"role": "tool", "tool_call_id": "c", "content": "tiny"}]}
    out, report = optimize(body, config(max_tool_result_chars=1000))
    assert out["messages"][0]["content"] == "tiny"
    assert report.actions == []


def test_report_serialises_for_dashboard():
    body = {"messages": [{"role": "user", "content": "a   \nb"}]}
    _, report = optimize(body, config())
    data = report.as_dict()
    assert set(data) == {"chars_before", "chars_after", "chars_saved", "tokens_saved", "actions"}
    assert data["chars_before"] == data["chars_after"] + data["chars_saved"]
    assert data["tokens_saved"] >= 0


def test_original_body_is_not_mutated():
    body = {
        "messages": [
            {"role": "system", "content": "a\n\na"},
            {"role": "system", "content": "a"},
            {"role": "tool", "tool_call_id": "c", "content": "x" * 100},
        ]
    }
    import copy

    snapshot = copy.deepcopy(body)
    turned = optimize(body, config(max_tool_result_chars=10))[0]
    assert body == snapshot
    assert turned is not body
