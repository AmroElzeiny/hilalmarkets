from ai_market_monitor.schemas.strategy_draft_v2 import SetupIntent
from hm_chatbot_eval.launch_core import launch_core_contracts, run_launch_core


def test_launch_core_is_fixed_zero_cost_semantic_replay(tmp_path):
    summary, run_dir = run_launch_core(tmp_path, run_id="test-launch-core")

    assert len(launch_core_contracts()) >= 13
    assert summary["stable_regression_status"] == "PASS"
    assert summary["critical_safety_status"] == "PASS"
    assert summary["exploratory_status"] == "NOT_MEASURED"
    assert summary["model_calls"] == 0
    assert summary["cost_usd"] == 0
    assert summary["semantic_accuracy"] == 1
    assert all(summary["structural_checks"].values())
    assert (run_dir / "report.html").exists()
    assert (run_dir / "report.md").exists()
    assert (run_dir / "summary.json").exists()


def test_setup_intent_enum_has_exact_launch_contract():
    assert {item.value for item in SetupIntent} == {
        "CONVERSATION",
        "PRODUCT_QUESTION",
        "STRATEGY_PATCH",
        "APPROVAL_ACTION",
        "EXPLANATION_REQUEST",
        "UNSUPPORTED_REQUEST",
    }
