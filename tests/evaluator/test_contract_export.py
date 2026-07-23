import subprocess
import sys


def test_exported_evaluator_contracts_are_current():
    result = subprocess.run(
        [sys.executable, "scripts/export_setup_chat_eval_contracts.py", "--check"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
