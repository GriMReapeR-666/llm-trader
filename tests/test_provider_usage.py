from types import SimpleNamespace

from operations.agent_operations import AgentOperations
from data.cerebras_client import _usage_metadata as cerebras_usage
from data.generate_with_openrouter import _usage_metadata as openrouter_usage


def _completion():
    return SimpleNamespace(
        usage=SimpleNamespace(prompt_tokens=17, completion_tokens=9, total_tokens=26, cost=0.0042),
        cost=None,
    )


def test_provider_usage_extractors_keep_only_numeric_metadata():
    expected = {
        "prompt_tokens": 17,
        "completion_tokens": 9,
        "total_tokens": 26,
        "cost": 0.0042,
        "source": "provider_response_usage",
    }
    assert openrouter_usage(_completion()) == expected
    assert cerebras_usage(_completion()) == expected


def test_provider_usage_does_not_accept_negative_or_nonfinite_cost():
    completion = SimpleNamespace(
        usage={"prompt_tokens": "bad", "completion_tokens": -1, "total_tokens": 4, "cost": float("nan")},
        cost=None,
    )
    result = openrouter_usage(completion)
    assert result == {"total_tokens": 4, "source": "provider_response_usage"}


def test_llm_request_persists_provider_tokens_and_cost(tmp_path):
    ops = AgentOperations(str(tmp_path / "state.db"))
    allowed, request_id = ops.reserve_llm(
        strategy="scalp", provider="test", model="test", trigger_key="usage:1",
        max_per_hour=10, max_per_day=10,
    )
    assert allowed
    ops.finish_llm(request_id, success=True, tokens=26, cost=0.0042)
    with ops._con() as con:
        row = con.execute("SELECT tokens,cost,status FROM llm_requests WHERE request_id=?", (request_id,)).fetchone()
    assert dict(row) == {"tokens": 26, "cost": 0.0042, "status": "SUCCESS"}

