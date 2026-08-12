"""Keep the engineering assistant out of the product, and the product out of it.

The separation between HilalMarkets' production AI and the Open Interpreter engineering
AI is the whole safety story of OI-1. It is currently true, and a convention that is
currently true is exactly the kind that stops being true one import at a time.

Two directions, and each is refused for its own reason:

``ai_market_monitor`` importing ``hm_oi``
    Would put an assistant that runs shell commands inside a code path a customer's
    request can reach. That is an unbounded code-execution surface attached to the
    product, and no amount of care elsewhere would make up for it.

``hm_oi`` importing ``ai_market_monitor``
    Would tie the engineering tool to the product's dependency pins and settings, and
    would let an engineering session load production configuration by accident. It also
    quietly makes ``hm_oi`` part of the application's import graph, which is how the
    first direction happens later.

Run by the release gate. Exits non-zero on the first violation with the file and line.
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRODUCT = ROOT / "src" / "ai_market_monitor"
ENGINEERING = ROOT / "src" / "hm_oi"

#: Modules the engineering assistant may import from the wider repository. Empty on
#: purpose: it uses the standard library and nothing else, which is what lets it run in a
#: separate environment with different Python and different pins.
ENGINEERING_ALLOWED_PROJECT_IMPORTS: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class Violation:
    path: Path
    line: int
    statement: str
    reason: str

    def render(self) -> str:
        relative = self.path.relative_to(ROOT).as_posix()
        return f"{relative}:{self.line}  {self.statement}\n    {self.reason}"


def _imported_names(tree: ast.AST) -> list[tuple[int, str, str]]:
    """Every module name imported in a file, with the line and the original statement."""

    found: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append((node.lineno, alias.name, f"import {alias.name}"))
        elif isinstance(node, ast.ImportFrom):
            # A relative import cannot cross package boundaries, so it is never a
            # violation and ``node.module`` would be misleading for one.
            if node.level:
                continue
            module = node.module or ""
            names = ", ".join(alias.name for alias in node.names)
            found.append((node.lineno, module, f"from {module} import {names}"))
    return found


def _scan(directory: Path, forbidden_prefix: str, reason: str) -> list[Violation]:
    violations: list[Violation] = []
    if not directory.is_dir():
        return violations
    for path in sorted(directory.rglob("*.py")):
        try:
            # ``utf-8-sig`` rather than ``utf-8``: a file saved by a Windows editor can
            # carry a byte-order mark, and ``ast.parse`` refuses the U+FEFF that plain
            # ``utf-8`` leaves at the front. Python's own import machinery strips it, so
            # such a file runs perfectly well — a checker that fell over on one would be
            # reporting its own limitation as a boundary violation.
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        except (OSError, SyntaxError) as exc:
            violations.append(Violation(path, 0, "<unreadable>", f"could not parse: {exc}"))
            continue
        for line, module, statement in _imported_names(tree):
            if module == forbidden_prefix or module.startswith(f"{forbidden_prefix}."):
                if module in ENGINEERING_ALLOWED_PROJECT_IMPORTS:
                    continue
                violations.append(Violation(path, line, statement, reason))
    return violations


def main() -> int:
    violations: list[Violation] = []

    violations += _scan(
        PRODUCT,
        "hm_oi",
        "The product must never import the engineering assistant. Doing so puts a tool "
        "that runs shell commands inside a path a customer request can reach.",
    )
    violations += _scan(
        ENGINEERING,
        "ai_market_monitor",
        "The engineering assistant must never import the product. It runs in a separate "
        "environment with a different Python version, and importing the application "
        "would load production configuration into an engineering session.",
    )

    if violations:
        print("The engineering/product boundary is broken:\n")
        for item in violations:
            print(item.render())
            print()
        print(f"{len(violations)} violation(s). See AGENTS.md section 1.")
        return 1

    print(
        f"Boundary intact: {PRODUCT.name} and {ENGINEERING.name} do not import each other."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
