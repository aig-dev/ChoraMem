"""Reuse unchanged legacy semantic cases with the same live diagnostic adapter."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from adaptive_cases import AdaptiveCase
from test_live_prompt_semantics import CASES as LEGACY_CASES

CASES = tuple(AdaptiveCase(
    case.name, case.window, case.allowed_targets, case.allowed_basis,
    tuple((c.target, c.operation) for c in case.expected),
    "保留旧例的形成、事实去重、反馈配对语义；形状通过不代替原文检查：" + case.name,
) for case in LEGACY_CASES)
