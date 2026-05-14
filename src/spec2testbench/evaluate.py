"""Automated evaluation of an extractor's output against a gold IR.

Provider-agnostic: takes two `TestPlan` instances and yields a verdict plus
a field-level diff list. Used both by the live extraction tests and by the
future benchmark harness.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator

from .ir import TestPlan, canonical_form, semantic_equivalent


@dataclass(frozen=True)
class EvaluationReport:
    """Result of comparing an extracted TestPlan to a gold one."""

    equivalent: bool
    differences: tuple[str, ...] = field(default_factory=tuple)

    def summary(self) -> str:
        if self.equivalent:
            return "✅ Extracted IR is semantically equivalent to gold."
        body = "\n".join(f"  - {d}" for d in self.differences)
        return f"❌ Extracted IR differs from gold ({len(self.differences)} diffs):\n{body}"


def evaluate_extraction(extracted: TestPlan, gold: TestPlan) -> EvaluationReport:
    """Compare `extracted` to `gold` and produce a structured report.

    `equivalent` is the boolean verdict from `semantic_equivalent` (ignores
    metadata, treats unordered lists as set-like). `differences` is empty
    when equivalent; otherwise lists per-path diffs against the canonical
    form, useful for narrowing down which field the LLM got wrong.
    """
    if semantic_equivalent(extracted, gold):
        return EvaluationReport(equivalent=True)
    a = canonical_form(extracted)
    b = canonical_form(gold)
    diffs = tuple(_diff_paths(a, b))
    return EvaluationReport(equivalent=False, differences=diffs)


def _diff_paths(a: Any, b: Any, path: str = "") -> Iterator[str]:
    """Yield human-readable diff strings between two canonical-form values.

    Operates on already-canonical dicts/lists (so unordered lists have been
    sorted upstream — positional diffs are stable). Not a perfect LCS-style
    diff for lists of different length; v0 keeps it simple and lists the
    raw mismatch.
    """
    if type(a) is not type(b):
        yield (
            f"{path or '<root>'}: type mismatch "
            f"(extracted={type(a).__name__}, gold={type(b).__name__})"
        )
        return

    if isinstance(a, dict):
        keys = sorted(set(a) | set(b))
        for k in keys:
            sub = f"{path}.{k}" if path else k
            if k not in a:
                yield f"{sub}: missing in extracted (gold has {b[k]!r})"
            elif k not in b:
                yield f"{sub}: extra in extracted (={a[k]!r})"
            else:
                yield from _diff_paths(a[k], b[k], sub)
        return

    if isinstance(a, list):
        if len(a) != len(b):
            yield f"{path}: list length differs (extracted={len(a)}, gold={len(b)})"
            return
        for i, (xa, xb) in enumerate(zip(a, b)):
            yield from _diff_paths(xa, xb, f"{path}[{i}]")
        return

    if a != b:
        yield f"{path}: extracted={a!r} gold={b!r}"
