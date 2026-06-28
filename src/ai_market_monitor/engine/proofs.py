from ai_market_monitor.engine.models import EvaluationResult


class ProofReceiptBuilder:
    def build(self, result: EvaluationResult) -> dict:
        receipt = result.proof_receipt()
        receipt["deterministic_source"] = "strategy_rule_engine"
        receipt["natural_language_guardrail"] = (
            "All values in this receipt were produced by deterministic evaluation."
        )
        return receipt
