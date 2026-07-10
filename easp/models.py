"""Data objects shared between the debugger and the UI."""

from __future__ import annotations

from dataclasses import dataclass, field


class QueryValue:
    """Truth value of an atom in the inspected answer set."""

    FALSE = 0
    TRUE = 1
    UNDEFINED = 2
    NOT_SET = 3


@dataclass(eq=True)
class QueryAtom:
    """An atom of the answer set together with its truth value.

    ``atom`` is the canonical clingo text of the atom (string constants use
    double quotes, e.g. ``reg("pat1","bed")``).
    """

    atom: str
    value: int

    # Shortcuts so callers can write QueryAtom.TRUE etc.
    FALSE = QueryValue.FALSE
    TRUE = QueryValue.TRUE
    UNDEFINED = QueryValue.UNDEFINED
    NOT_SET = QueryValue.NOT_SET

    def __post_init__(self) -> None:
        if self.value not in {self.FALSE, self.TRUE, self.UNDEFINED}:
            self.value = self.NOT_SET

    def __str__(self) -> str:
        return f"not {self.atom}" if self.value == self.FALSE else self.atom


@dataclass(frozen=True)
class Response:
    """One element of an explanation produced by the debugger.

    ``type`` values: 0 = rule, 1 = fact, 2 = answer-set literal,
    3 = aggregate rule (expandable in the UI).
    """

    rule: str
    type: int

    def __str__(self) -> str:
        return self.rule


@dataclass
class UnsatisfiableCore:
    """Minimal set of responses explaining an incoherence."""

    rules: list[Response] = field(default_factory=list)

    def add_rule(self, rule: str, type_: int) -> None:
        self.rules.append(Response(rule, type_))

    def get_rules(self) -> list[Response]:
        return self.rules


@dataclass(frozen=True)
class CostLevel:
    """Total cost of the weak constraints at one optimization level."""

    level: str
    cost: int

    def __str__(self) -> str:
        return f"Cost of {self.cost} at level {self.level}"
