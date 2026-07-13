"""High-level facade used by the UI and the tests.

``Justifier`` wraps a ``Debugger`` for one program; the small ``*Service``
classes mirror the JavaFX services of the original application and simply
forward to the corresponding ``Justifier`` method.
"""

from __future__ import annotations

from . import asp_parser
from .config import Settings
from .debugger import Debugger
from .models import CostLevel, QueryAtom, Response


class Justifier:
    """One debugging session for a single ASP program."""

    def __init__(
        self,
        program: str,
        debug_rules: bool,
        debug_answer_set: bool,
        settings: Settings | None = None,
    ) -> None:
        # The debugger pipeline is line-based: put every statement on its own
        # line first (rules written across several lines are merged).
        self.program = asp_parser.normalize_program(program)
        self.debugger = Debugger(debug_rules, debug_answer_set, self.program, settings=settings)
        #: Literals of the answer set currently being inspected.
        self.query_atoms: list[QueryAtom] = []

    def derive_query_atom(self, atom: str) -> QueryAtom:
        """Resolve a literal text (as shown to the user, e.g. ``not p('a').``)
        to the corresponding QueryAtom of the inspected answer set.

        Texts coming from debugger responses use single quotes for string
        constants; canonical atoms use double quotes, so quotes are restored
        before matching. Unknown literals default to a false atom.
        """
        normalized = atom.strip().removeprefix("not ").removesuffix(".").replace("'", '"').strip()
        for query_atom in self.query_atoms:
            if query_atom.atom == normalized:
                return query_atom
        return QueryAtom(normalized, QueryAtom.FALSE)

    def get_answer_set(self) -> list[QueryAtom]:
        return self.query_atoms

    def compute_answer_sets(self, n: int) -> list[str] | None:
        """Solve the program; returns the answer sets or None when unsat."""
        is_sat = self.debugger.compute_answer_sets(self.program, n)
        return self.debugger.get_answer_sets() if is_sat else None

    def retrieve_atoms(self, index: int) -> list[QueryAtom]:
        """Load the literals of the ``index``-th answer set for inspection."""
        self.debugger.get_facts(self.program)
        self.debugger.compute_atoms_derived(index)
        self.query_atoms = self.debugger.populate_query()
        return self.query_atoms

    def request_cost_level(self) -> list[CostLevel]:
        return self.debugger.get_cost_level()

    def opt_problem(self) -> bool:
        return self.debugger.is_opt()

    def justify(self, chain: list[QueryAtom], atom: QueryAtom, check_opt: bool) -> list[Response]:
        """Explain why ``atom`` has its truth value in the inspected answer set."""
        core = self.debugger.debug_atom(atom, chain, self.query_atoms, self.program, check_opt)
        return core.get_rules()

    def justify_cost(self, level: CostLevel, check_opt: bool) -> list[Response]:
        """Explain the cost paid at one optimization level."""
        core = self.debugger.debug_cost(level.level, self.query_atoms, self.program, check_opt)
        return core.get_rules()

    def debug(self) -> list[Response]:
        """Explain why the program is unsatisfiable."""
        core = self.debugger.debug_program(self.program)
        return core.get_rules()

    def expand_aggregate(self, rule: str) -> dict[str, dict[str, list[str]]]:
        """Expand an aggregate and label each element with its bindings.

        The debugger exposes the grounded tuple values as comma-separated
        identifiers. Pair them with the tuple terms written in the source
        aggregate so the UI can show labels such as ``<D=2, PH=1>``.
        """
        expanded = self.debugger.generate_set(rule)
        expressions = asp_parser.aggregate_expressions(rule)

        labelled: dict[str, dict[str, list[str]]] = {}
        for instance, groups in expanded.items():
            element_terms = self._aggregate_terms_for_instance(expressions, instance)
            if not element_terms:
                labelled[instance] = groups
                continue
            labelled_groups: dict[str, list[str]] = {}
            for group_id, atoms in groups.items():
                label = self._aggregate_binding_label(element_terms, group_id)
                labelled_groups.setdefault(label, []).extend(atoms)
            labelled[instance] = labelled_groups
        return labelled

    @staticmethod
    def _aggregate_terms_for_instance(expressions: list[str], instance: str) -> list[str]:
        for expression in expressions:
            core = asp_parser.aggregate_core(expression)
            if core and core in instance:
                return asp_parser.aggregate_element_terms(expression)
        return []

    @staticmethod
    def _aggregate_binding_label(element_terms: list[str], group_id: str) -> str:
        values = asp_parser.split_top_level(group_id)
        if len(values) != len(element_terms):
            return group_id

        bindings: list[str] = []
        for term, value in zip(element_terms, values):
            clean_term = term.strip()
            clean_value = value.strip()
            if asp_parser.variables_of(clean_term):
                bindings.append(f"{clean_term}={clean_value}")
            else:
                bindings.append(clean_value)
        return f"<{', '.join(bindings)}>"

    def truth_aggregate(self, rule: str, external: str) -> str:
        return self.debugger.get_truth_aggregate(rule, external)

    def aggregate_uses_exact_comparison(self, text: str) -> bool:
        return self.debugger.aggregate_uses_exact_comparison(text)


class ComputeAnswerSetsService:
    def __init__(self, justifier: Justifier, n: int) -> None:
        self.justifier = justifier
        self.n = n

    def run(self) -> list[str] | None:
        return self.justifier.compute_answer_sets(self.n)


class RetrieveAtomsService:
    def __init__(self, justifier: Justifier, index: int) -> None:
        self.justifier = justifier
        self.index = index

    def run(self) -> list[QueryAtom]:
        return self.justifier.retrieve_atoms(self.index)


class ExplainAtomService:
    def __init__(
        self,
        justifier: Justifier,
        chain: list[QueryAtom],
        atom: QueryAtom,
        check_opt: bool = False,
    ) -> None:
        self.justifier = justifier
        self.chain = chain
        self.atom = atom
        self.check_opt = check_opt

    def run(self) -> list[Response]:
        return self.justifier.justify(self.chain, self.atom, self.check_opt)


class ExplainCostService:
    def __init__(self, justifier: Justifier, level: CostLevel, check_opt: bool = True) -> None:
        self.justifier = justifier
        self.level = level
        self.check_opt = check_opt

    def run(self) -> list[Response]:
        return self.justifier.justify_cost(self.level, self.check_opt)


class DebugProgramService:
    def __init__(self, justifier: Justifier) -> None:
        self.justifier = justifier

    def run(self) -> list[Response]:
        return self.justifier.debug()
