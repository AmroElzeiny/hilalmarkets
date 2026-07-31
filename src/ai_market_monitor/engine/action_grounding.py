"""Central multilingual action vocabulary for deterministic plan verification.

The AI proposes an operation. These patterns only verify that the source segment is
compatible with that operation; they never infer an operation or mutate state.
"""

from __future__ import annotations

import re
from typing import Literal

SemanticAction = Literal[
    "include",
    "exclude",
    "remove_inclusion",
    "remove_exclusion",
    "remove_condition",
    "remove_unsupported",
    "restore",
    "required",
    "optional",
    "clear",
    "change",
]

_ACTION_PATTERNS: dict[SemanticAction, str] = {
    "include": (
        r"\b(?:include|add|only|watch|monitor|scan)\b"
        r"|(?:ضم|اضف|أضف|راقب|تابع|اسمح)"
        r"|\b(?:dof|deef|add|ra2eb|tab3)\b"
    ),
    "exclude": (
        r"\b(?:exclud(?:e|ed|es|ing)|avoid(?:ed|s|ing)?|block(?:ed|s|ing)?|except)\b"
        r"|\b(?:do not|don't|never)\s+(?:include|watch|monitor|scan)\b"
        r"|(?:استبعد|تجنب|امنع|ماعدا|إلا)"
        r"|\b(?:estab3ed|mat7otsh|tganab|emn3)\b"
    ),
    "remove_inclusion": (
        r"\b(?:remove|drop|stop including|no longer include|stop watching)\b"
        r"|(?:احذف|شيل|وقف متابعة|ما تراقبش)"
        r"|\b(?:sheel|e7zef|wa2af|matra2ebsh)\b"
    ),
    "remove_exclusion": (
        r"\b(?:allow|unblock|stop excluding|include again)\b"
        r"|(?:اسمح|الغ الاستبعاد|ألغي الاستبعاد|رجع)"
        r"|\b(?:esma7|el8y|raga3)\b"
    ),
    "remove_condition": (
        r"\b(?:remove|delete|drop|undo|without|stop using)\b"
        r"|(?:احذف|شيل|الغي|ألغي|من غير|بلاش)"
        r"|\b(?:e7zef|sheel|el8y|men 8eer|balash)\b"
    ),
    "remove_unsupported": (
        r"\b(?:drop|remove|replace|forget|do not use|don't use)\b"
        r"|(?:احذف|شيل|بدل|انسى|ما تستخدمش)"
        r"|\b(?:sheel|e7zef|badal|ensa|matesta5demsh)\b"
    ),
    "restore": (
        r"\b(?:undo|restore|go back|return to|revert)\b"
        r"|(?:ارجع|رجع|استرجع|الغي التغيير|ألغي التغيير)"
        r"|\b(?:erga3|raga3|estarge3|undo)\b"
    ),
    "required": (
        r"\b(?:required|must|need|mandatory)\b"
        r"|(?:مطلوب|لازم|إجباري)"
        r"|\b(?:lazem|matloob|egbary)\b"
    ),
    "optional": (
        r"\b(?:optional|not required|prefer|nice to have)\b"
        r"|(?:اختياري|مش لازم|يفضل)"
        r"|\b(?:ekhtiyary|msh lazem|yefdal)\b"
    ),
    "clear": (
        r"\b(?:clear|remove|unset|stop using|no longer use|without|none|neutral)\b"
        r"|(?:امسح|احذف|شيل|الغي|ألغي|من غير|محايد)"
        r"|\b(?:ems7|e7zef|sheel|el8y|men 8eer|neutral)\b"
    ),
    "change": (
        r"\b(?:change|make|set|update|correct|instead|only)\b"
        r"|(?:غير|غيّر|خلي|صحح|بدل|فقط|بس)"
        r"|\b(?:8ayar|ghayar|5aly|khally|sa7a7|badal|bas)\b"
    ),
}


def action_is_grounded(text: str, action: SemanticAction) -> bool:
    """Whether the segment explicitly supports the proposed action."""

    return bool(re.search(_ACTION_PATTERNS[action], text.casefold(), re.IGNORECASE))
