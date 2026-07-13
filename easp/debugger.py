"""Core debugging engine (Python port of the Java ``Debugger`` class).

The debugger explains why a literal is (or is not) in an answer set, or why a
program is incoherent, by *instrumenting* the program:

- every rule/fact gets an extra guard literal ``__debug(...)`` that allows
  the solver to "switch the rule off";
- every literal of the inspected answer set gets a ``__support(...)`` choice
  that allows the solver to re-derive it without a rule;
- the literal to explain is forced to the opposite truth value, making the
  instrumented program incoherent.

clingo is then asked for a *minimal* set of guards that cannot be disabled
without restoring coherence (a minimal unsatisfiable core). The rules and
literals named by that core are the explanation shown to the user.

Encoding conventions used by the instrumentation:

- ``__debug("<rule>",<type>,<line>)``  -- guard of a rule (type 0), fact
  (type 1) or answer-set literal (type 2).
- ``__debug("<rule>",3,start,"VAR",<value>,...,end,0)`` -- guard of a rule
  containing ``#count``/``#sum``. One ground guard is generated per ground
  instance so the core can point at the exact instance that fired.
- ``__support("<constraint>",0,<line>)`` -- support of one answer-set literal.
- The rule text embedded in those string constants has its double quotes
  replaced by single quotes so the program stays parseable; whenever such
  text is compared against real atoms the quotes are restored with
  ``_restore_quotes``. The rules *emitted* into the program always keep the
  original double quotes (clingo does not accept single-quoted strings).
"""

from __future__ import annotations

import re
from collections import OrderedDict
from typing import Iterable

from . import asp_parser
from .clingo_runner import ClingoRunner, SolveSummary
from .config import Settings
from .models import CostLevel, QueryAtom, UnsatisfiableCore


class Debugger:
    def __init__(
        self,
        debug_rules: bool,
        debug_answer_set: bool,
        program: str,
        settings: Settings | None = None,
    ) -> None:
        self.runner = ClingoRunner(settings)
        #: Instrumentation atoms of the current extended program.
        self.debug_atoms: list[str] = []
        #: Weak-constraint bookkeeping (body text -> aux atom, level -> cost...).
        self.weak_to_aux: dict[str, str] = {}
        self.level_to_cost: dict[str, int] = {}
        self.level_to_aux: dict[str, list[list[str]]] = {}
        #: Atoms that are facts of the input program.
        self.initial_facts: list[str] = []
        #: True atoms of the inspected answer set that are not facts.
        self.derived_atoms: list[str] = []
        #: Grounded atoms that are false in the inspected answer set.
        self.false_atoms: list[str] = []
        #: Rules of the input program (without trailing dot).
        self.rules: list[str] = []
        #: Solver assignment order for the inspected answer set.
        self.order: list[str] = []
        #: All grounded head atoms of the last solve call.
        self.grounded: list[str] = []
        #: Atom currently being explained.
        self.analyzed: QueryAtom | None = None
        self.debug_rules = debug_rules
        self.debug_answer_set = debug_answer_set
        self.optimization_problem = False
        self.output: SolveSummary | None = None
        self.n_models = 1
        #: Atoms that never occur in a rule head (reported by gringo).
        self.unsupported: list[str] = []
        #: ``unsupported`` plus heads of rules that depend on them; they are
        #: false in every answer set but never appear among grounded heads.
        self.unsupported_false: list[str] = []
        #: Cache of aggregate truth messages: rule text -> instance -> message.
        self.truth_aggregate: dict[str, dict[str, str]] = {}
        #: Extended program of the last core computation (for search_head).
        self._current_extended_program = ""
        self.check_opt(program)

    def stop_debug(self) -> None:
        """Kept for API compatibility: solver calls are short-lived."""
        return

    # ------------------------------------------------------------------
    # Solving and answer-set bookkeeping
    # ------------------------------------------------------------------

    def compute_answer_sets(self, program: str, n: int) -> bool:
        """Solve the original program asking for ``n`` answer sets.

        Returns True when the program is satisfiable. The models, the
        grounded heads and the assignment orders are stored for later use.
        """
        self.derived_atoms = []
        self.false_atoms = []
        self.level_to_cost = {}
        self.level_to_aux = {}
        self.weak_to_aux = {}
        self.unsupported = []
        self.unsupported_false = []
        self.n_models = n
        # clingo must never see the user annotations.
        program = self._apply_annotations(program)

        if self.optimization_problem:
            # Add aux atoms so the cost of each weak constraint instance can
            # be read back from the model. The runner returns only proven
            # optimal models, so no extra bookkeeping is needed here.
            tmp_program = self.add_aux_program(program)
            summary = self.runner.solve(
                tmp_program,
                models=n,
                optimization=True,
                check_error=True,
            )
        else:
            summary = self.runner.solve(program, models=n, check_error=True)

        self.grounded = summary.heads
        self.output = summary
        if summary.unsupported_atoms:
            self._register_unsupported(summary.unsupported_atoms, program)
        return not summary.unsatisfiable and summary.satisfiable

    def get_answer_sets(self) -> list[str]:
        """Answer sets of the last solve call, one comma-separated string each.

        For optimization problems the runner already filters out the
        intermediate models found while optimizing, so every witness is a
        proven optimal answer set."""
        if self.output is None:
            return []
        # aux(...) atoms are internal cost bookkeeping: hide them.
        return [
            ", ".join(atom for atom in witness if not atom.startswith("aux("))
            for witness in self.output.witnesses
        ]

    def compute_atoms_derived(self, selected_index: int) -> None:
        """Load derived/false atoms and assignment order of the answer set
        chosen by the user."""
        if self.output is None:
            return
        index = min(selected_index, max(0, len(self.output.witnesses) - 1))
        witness = self.output.witnesses[index] if self.output.witnesses else []
        self.order = self.output.orders[index] if index < len(self.output.orders) else []
        self._populate_atoms_from_witness(witness, self.grounded)

    def populate_query(self) -> list[QueryAtom]:
        """Build the list of literals the user can inspect: derived atoms as
        true literals, grounded-but-absent atoms as false literals."""
        query_atoms: list[QueryAtom] = []
        for atom in self.derived_atoms:
            if not atom.startswith("aux("):  # aux atoms are internal (costs)
                query_atoms.append(QueryAtom(atom, QueryAtom.TRUE))
        for atom in self.false_atoms:
            if not atom.startswith("aux(") and atom not in self.unsupported:
                query_atoms.append(QueryAtom(atom, QueryAtom.FALSE))
        return query_atoms

    def get_facts(self, program: str) -> None:
        """Ground the fact-only part of the program to collect the initial
        facts (intervals like ``mss(1,1..9).`` are expanded by gringo)."""
        fact_lines: list[str] = []
        self.initial_facts = []
        self.rules = []
        for raw_line in self._apply_annotations(program).splitlines():
            line = asp_parser.strip_line_comment(raw_line).strip()
            if not line or line.startswith("%"):
                continue
            if line.startswith("#") and not line.startswith("#const"):
                continue  # directives such as #show are not facts
            if ":-" not in line and ":~" not in line and "{" not in line and "|" not in line:
                fact_lines.append(line)  # ("|" lines are disjunctive rules, not facts)
            elif ":-" in line:
                self.rules.append(line[: max(0, len(line) - 1)])

        if fact_lines:
            output = self.runner.ground_text("\n".join(fact_lines) + "\n")
            for atom in output.splitlines():
                atom = atom.strip()
                if atom:
                    self._add_unique(self.initial_facts, atom[:-1] if atom.endswith(".") else atom)

    def check_opt(self, program: str) -> None:
        """Detect whether the program is an optimization problem and record
        the optimization levels declared by its weak constraints, so a level
        is shown (with cost 0) even when the optimum does not pay it."""
        self.optimization_problem = False
        self.declared_levels: list[str] = []
        for raw_line in self._apply_annotations(program).splitlines():
            line = raw_line.strip()
            if not line.startswith((":~", "#minimize", "#maximize")):
                continue
            self.optimization_problem = True
            try:
                level = self.read_costs(line)[2]
            except Exception:
                continue
            # Only constant levels can be listed (a variable level such as
            # [C@X] is known only after grounding).
            if re.fullmatch(r"-?\d+|[a-z]\w*", level) and level not in self.declared_levels:
                self.declared_levels.append(level)

    def is_opt(self) -> bool:
        return self.optimization_problem

    def get_cost_level(self) -> list[CostLevel]:
        return [CostLevel(level, cost) for level, cost in self.level_to_cost.items()]

    # ------------------------------------------------------------------
    # Entry points of the three debugging modes
    # ------------------------------------------------------------------

    def debug_atom(
        self,
        atom: QueryAtom,
        chain: list[QueryAtom],
        queries: list[QueryAtom],
        program: str,
        check_opt: bool,
    ) -> UnsatisfiableCore:
        """Explain why ``atom`` has its truth value in the answer set."""
        self.analyzed = atom
        program = self.add_derived(program, atom, chain, queries)
        program = self.set_rules_for_order(program, atom)
        extended_program = self.extend_program(program, atom, "", check_opt)
        return self.compute_minimal_core(extended_program, atom, check_opt)

    def debug_cost(
        self,
        level: str,
        queries: list[QueryAtom],
        program: str,
        check_opt: bool,
    ) -> UnsatisfiableCore:
        """Explain why the answer set pays its cost at ``level``."""
        program = self.add_derived_for_queries(program, queries)
        extended_program = self.extend_program(program, self.analyzed, level, check_opt)
        return self.compute_minimal_core(extended_program, None, check_opt)

    def debug_program(self, program: str) -> UnsatisfiableCore:
        """Explain why the program has no answer set."""
        self.get_facts(program)
        extended_program = self.extend_program(program, None, "", False)
        return self.compute_minimal_core_unsat(extended_program)

    # ------------------------------------------------------------------
    # Program preparation (before instrumentation)
    # ------------------------------------------------------------------

    def set_rules_for_order(self, program: str, atom: QueryAtom) -> str:
        """Mark with ``@ignore`` every line that mentions an atom assigned by
        the solver *after* the analyzed atom: those assignments cannot be part
        of the reason the analyzed atom got its value."""
        later_atoms: list[str] = []
        if atom.atom in self.order:
            seen = False
            for item in self.order:
                if item == atom.atom:
                    seen = True
                    continue
                if seen:
                    later_atoms.append(item)

        # Match whole atoms, not substrings: with an atom named "d" a naive
        # substring test would also hit unrelated text (even the
        # "%Add Answer Set" marker, silently dropping the whole section).
        # Order entries may carry a "not " prefix added by the propagator.
        patterns = [
            re.compile(
                r"(?<![A-Za-z0-9_])"
                + re.escape(item.removeprefix("not ").strip())
                + r"(?![A-Za-z0-9_(])"
            )
            for item in later_atoms
            if item.strip()
        ]

        builder: list[str] = []
        for raw_line in program.splitlines():
            # Match (and tag) the line without its inline comment, so a word
            # inside a comment cannot trigger the @ignore and the tag itself
            # cannot end up inside a comment.
            line = asp_parser.strip_line_comment(raw_line)
            stripped = line.strip()
            if stripped and not stripped.startswith("%") and any(p.search(line) for p in patterns):
                builder.append(f"{line}@ignore")
            else:
                builder.append(raw_line)
        return "\n".join(builder) + "\n"

    def add_derived(
        self,
        program: str,
        atom: QueryAtom,
        chain: list[QueryAtom],
        queries: list[QueryAtom],
    ) -> str:
        """Append one constraint per answer-set literal, freezing the answer
        set. The analyzed atom gets the *opposite* constraint (which makes the
        program incoherent); atoms already explained in the chain are ignored."""
        builder = ["", "%Add Answer Set"]
        for query in queries:
            if query.atom in self.unsupported:
                continue
            if query == atom:
                # Force the opposite value of the atom under analysis.
                builder.append(f":- not {query.atom}." if query.value == QueryAtom.FALSE else f":- {query.atom}.")
            elif query in chain:
                builder.append(
                    f":- {query.atom}.@ignore"
                    if query.value == QueryAtom.FALSE
                    else f":- not {query.atom}.@ignore"
                )
            else:
                # Freeze the literal to its value in the answer set.
                builder.append(f":- {query.atom}." if query.value == QueryAtom.FALSE else f":- not {query.atom}.")
        return program + "\n".join(builder) + "\n"

    def add_derived_for_queries(self, program: str, queries: list[QueryAtom]) -> str:
        """Freeze the whole answer set (used for cost explanations)."""
        builder = ["", "%Add Answer Set"]
        for query in queries:
            builder.append(f":- {query.atom}." if query.value == QueryAtom.FALSE else f":- not {query.atom}.")
        return program + "\n".join(builder) + "\n"

    def add_aux_program(self, program: str) -> str:
        """Add ``aux(discriminant,cost,level) :- body`` next to every weak
        constraint, so costs can be read back from the answer set."""
        builder: list[str] = []
        for line in program.splitlines():
            builder.append(line)
            if line.strip().startswith((":~", "#minimize", "#maximize")):
                costs = self.read_costs(line)
                aux = self.generate_aux(costs)
                tmp_body = self.generate_body(line)
                costs.append(tmp_body)
                builder.append(aux + " :- " + tmp_body + " .")
        return "\n".join(builder) + "\n"

    # ------------------------------------------------------------------
    # Instrumentation
    # ------------------------------------------------------------------

    def extend_program(
        self,
        program: str,
        atom: QueryAtom | None,
        level: str,
        check_opt: bool,
    ) -> str:
        """Build the instrumented ("extended") program.

        Lines before the ``%Add Answer Set`` marker are the program rules;
        lines after it are the freezing constraints added by ``add_derived``.
        """
        self.debug_atoms = []
        if check_opt:
            # Rebuilt below from the weak constraints of this program; without
            # the reset repeated explanations would accumulate duplicates.
            self.level_to_aux = {}
            self.weak_to_aux = {}
        builder: list[str] = []
        in_answer_set = False
        for cont, raw_line in enumerate(program.splitlines(), start=1):
            line = raw_line.strip()
            if line.startswith("%@description: "):
                continue
            if line == "%Add Answer Set":
                in_answer_set = True
                continue
            if line.startswith("%") or not line:
                continue
            if "#const" in line:
                builder.append(line)
                continue
            if line.startswith("#") and not line.startswith(("#minimize", "#maximize")):
                # #show and other directives must not be instrumented as facts.
                continue
            if "@ignore" in line:
                # Ignored rules (user annotation or internal marker) are
                # removed from the debugging program entirely.
                continue
            if "@correct" in line:
                # Trusted rule: kept active but WITHOUT a guard, so it can
                # never be blamed -- blame flows through it to its premises.
                builder.append(line.replace("@correct", "").strip())
                continue
            # Inline comments would otherwise be instrumented into the rule.
            line = asp_parser.strip_line_comment(line).strip()
            if not line:
                continue

            # Version of the rule embeddable inside a "..." string constant.
            line_parsed = line.replace('"', "'")

            if not in_answer_set:
                if line.startswith((":~", "#minimize", "#maximize")):
                    if check_opt:
                        # Replace the weak constraint with an aux rule; the
                        # final #sum constraints (below) recreate its effect.
                        costs = self.read_costs(line)
                        aux = self.generate_aux(costs)
                        tmp_body = self.generate_body(line)
                        costs.append(tmp_body)
                        self.weak_to_aux[tmp_body] = aux
                        self.level_to_aux.setdefault(costs[2], []).append(costs)
                        builder.append(aux + " :- " + tmp_body + " .")
                    continue

                if self.debug_rules:
                    builder.extend(self._instrument_source_rule(line, line_parsed, cont, program))
                else:
                    builder.append(line)
                continue

            # --- answer-set section: one freezing constraint per literal ---
            sup = f'__support("{line_parsed}",0,{cont})'
            self.debug_atoms.append(sup)
            # startswith, not substring: an atom named e.g. notification(1)
            # must not be mistaken for a negated literal.
            if line.startswith(":- not "):
                supported = line[len(":- not ") :]
            elif line.startswith(":- "):
                supported = line[len(":- ") :]
            else:
                continue

            # Choice that lets the solver re-derive the literal "for free";
            # paying `sup` in the core means the literal itself is the reason.
            # NOTE: `supported` keeps the original double quotes -- only the
            # text inside __debug/__support strings uses single quotes.
            builder.append(supported[:-1] + " :- " + sup + ".")
            builder.append("{" + sup + "}.")
            supported_parsed = supported.replace('"', "'")
            deb = f'__debug("{supported_parsed}",2,{cont})'
            self.debug_atoms.append(deb)

            if self.debug_answer_set:
                # Guarded freezing constraint: disabling `deb` relaxes it.
                builder.append(line[:-1] + ", not " + deb + ".")
                builder.append("{" + deb + "}.")
            elif not check_opt:
                builder.append(line)

        if not check_opt and atom is not None:
            # Safety net: force the opposite value of the analyzed atom (the
            # same constraint added by add_derived, kept for the cost path).
            if atom.value == QueryAtom.TRUE:
                builder.append(f":- {atom.atom}.")
            else:
                builder.append(f":- not {atom.atom}.")

        if check_opt:
            # Optimality explanation: require that the cost at the analyzed
            # level gets worse (>=) while every other level keeps its cost.
            for opt_level, aux_values in self.level_to_aux.items():
                fragments: list[str] = []
                for aux in aux_values:
                    fragment = aux[1]
                    if aux[0] != "":
                        fragment += "," + aux[0]
                    fragment += ":" + self.generate_aux(aux)
                    fragments.append(fragment)
                comparator = ">=" if level == opt_level else "!="
                builder.append(
                    ":- #sum{"
                    + "; ".join(fragments)
                    + f"}} {comparator} {self.level_to_cost.get(opt_level, 0)}."
                )

        return "\n".join(builder) + "\n"

    def _instrument_source_rule(
        self,
        line: str,
        line_parsed: str,
        line_number: int,
        grounding_program: str,
    ) -> list[str]:
        """Instrument one program rule/fact with its ``__debug`` guard."""
        if ":-" in line:
            if self._contains_aggregate(line):
                return self._instrument_aggregate_rule(line, line_parsed, grounding_program)

            debug_atom = f'__debug("{line_parsed}",0,{line_number})'
            self.debug_atoms.append(debug_atom)
            return [
                # rule body gets ", not __debug(...)": choosing __debug true
                # disables the rule.
                line[:-1] + ", not " + debug_atom + ".",
                "{" + debug_atom + "}.",
            ]

        if "." not in line:
            return []

        # Facts are type 1; choice/disjunctive facts count as rules (type 0).
        type_ = 0 if "{" in line or "|" in line else 1
        debug_atom = f'__debug("{line_parsed}",{type_},{line_number})'
        self.debug_atoms.append(debug_atom)
        return [
            line[:-1] + ":- not " + debug_atom + ".",
            "{" + debug_atom + "}.",
        ]

    def _instrument_aggregate_rule(
        self,
        line: str,
        line_parsed: str,
        grounding_program: str,
    ) -> list[str]:
        """Instrument a rule containing #count/#sum.

        A single non-ground guard would hide *which* instance of the rule
        fired, so one ground ``__debug`` choice is generated per instance
        (enumerated by grounding the rule body without the aggregate)."""
        template, debug_choices = self._aggregate_debug_atoms(line, line_parsed, grounding_program)
        self.debug_atoms.extend(debug_choices)
        return [
            line[:-1] + ", not " + template + ".",
            *["{" + debug_atom + "}." for debug_atom in debug_choices],
        ]

    def _aggregate_debug_atoms(
        self,
        line: str,
        line_parsed: str,
        grounding_program: str,
    ) -> tuple[str, list[str]]:
        """Return the non-ground guard template of an aggregate rule and its
        ground instances.

        The instances are enumerated by grounding ``{template} :- guard``
        together with the rest of the program, where ``guard`` is the rule
        body without the aggregate. The guard is taken from the *raw* line so
        string constants keep valid double quotes."""
        template = self._aggregate_debug_template(line_parsed)
        guard = self.get_external(line).strip().rstrip(".")
        choice_rule = "{" + template + "}" + (f" :- {guard}" if guard else "") + "."
        grounding_input = self._grounding_context(grounding_program).rstrip() + "\n" + choice_rule + "\n"

        debug_atoms: list[str] = []
        for grounded_line in self.runner.ground_text(grounding_input).splitlines():
            debug_atom = self._debug_atom_from_choice_line(grounded_line)
            if debug_atom and debug_atom not in debug_atoms:
                debug_atoms.append(debug_atom)

        if not debug_atoms and not self._aggregate_debug_variables(line_parsed):
            # No instance found but the rule has no global variables: the
            # template itself is ground and can be used directly. (With
            # variables and no instances the rule can never fire, so no
            # guard choice is needed at all.)
            debug_atoms.append(template)
        return template, debug_atoms

    def _aggregate_debug_template(self, line_parsed: str) -> str:
        """Build ``__debug("<rule>",3,start,"VAR",VAR,...,end,0)`` carrying the
        global variables of the rule, so their values survive into the core."""
        debug_atom = f'__debug("{line_parsed}",3,start'
        for variable in self._aggregate_debug_variables(line_parsed):
            debug_atom += f',"{variable}",{variable}'
        return debug_atom + ",end,0)"

    def _aggregate_debug_variables(self, line_parsed: str) -> list[str]:
        """Variables usable in the aggregate guard template.

        Only variables occurring in regular body literals are returned:
        variables bound by the aggregate itself (e.g. ``DUR`` in
        ``DUR = #sum{...}``) and the anonymous variable ``_`` must be
        excluded, otherwise the instrumented program would not be safe."""
        variables: list[str] = []
        for terms in asp_parser.search_terms(line_parsed).values():
            for term in terms:
                for variable in asp_parser.variables_of(term):
                    if variable not in variables:
                        variables.append(variable)
        return variables

    @staticmethod
    def _debug_atom_from_choice_line(line: str) -> str | None:
        """Extract the __debug atom from a grounded ``{__debug(...)}`` line
        (tolerating optional spaces after the brace)."""
        line = line.strip()
        if not line.startswith("{") or not line[1:].lstrip().startswith("__debug("):
            return None
        end = line.rfind("}")
        if end < 0:
            return None
        return line[1:end].strip()

    @staticmethod
    def _grounding_context(program: str) -> str:
        """Program text used as context when grounding helper rules
        (annotations removed, @ignore'd statements dropped)."""
        lines: list[str] = []
        for raw_line in program.splitlines():
            if "@ignore" in raw_line:
                continue
            line = raw_line.replace("@correct", "").strip()
            if line:
                lines.append(line)
        return "\n".join(lines)

    @staticmethod
    def _apply_annotations(program: str) -> str:
        """Program as clingo must see it when solving: statements annotated
        with ``@ignore`` are removed, ``@correct`` markers are stripped (the
        rule itself stays active)."""
        lines: list[str] = []
        for line in program.splitlines():
            if "@ignore" in line:
                continue
            lines.append(line.replace("@correct", "").rstrip())
        return "\n".join(lines) + "\n"

    @staticmethod
    def _contains_aggregate(line: str) -> bool:
        return "#count" in line or "#sum" in line

    def get_external(self, rule: str) -> str:
        """Body of ``rule`` with the aggregate (and its guard) removed."""
        body = rule.split(":-")[-1]
        pattern = r",?\s*(\w+?\s*(?:!=|=|<=|>=|<|>))?\s*#(count|sum)\{[^}]*\}\s*((!=|=|<=|>=|<|>)\s*\w+)?\s*(?:,|.){1}"
        return re.sub(pattern, "", body)

    # ------------------------------------------------------------------
    # Minimal core computation
    # ------------------------------------------------------------------

    def check_coherence(self, extended_program: str, core: Iterable[str]) -> bool:
        """True when the extended program is coherent once every guard in
        ``core`` is forced to false (i.e. the corresponding rules stay ON)."""
        tmp = extended_program
        for atom in core:
            # Only real #show directives must be skipped -- a plain substring
            # test would also skip guards of rules mentioning e.g. showroom(X).
            if "#show" not in atom:
                tmp += f":- {atom}.\n"
        return not self.runner.is_incoherent(tmp, "--outf=1", "--keep-facts")

    def compute_minimal_core(
        self,
        extended_program: str,
        atom: QueryAtom | None,
        check_opt: bool,
        expand_constraints: bool = False,
    ) -> UnsatisfiableCore:
        """Compute a minimal unsatisfiable core and translate it into the
        Responses shown to the user.

        ``expand_constraints`` (used by the unsat-debugging flow) also adds,
        for every constraint in the core, the rules defining the atoms the
        constraint requires to be true; see
        ``_add_defining_rules_for_core_constraints``."""
        unsat_core = UnsatisfiableCore()
        self._current_extended_program = extended_program

        # Atoms that never occur in a head have a trivial explanation.
        if not check_opt and atom is not None and atom.atom in self.unsupported:
            unsat_core.add_rule("No rules with atom in the head", 0)
            return unsat_core

        # Coherent even with every rule enabled: nothing to explain.
        if self.check_coherence(extended_program, self.debug_atoms):
            return unsat_core

        minimal_core = self._minimal_core(extended_program)
        self._add_debug_rules_to_core(unsat_core, minimal_core, atom=atom)
        if expand_constraints and self.debug_rules:
            self._add_defining_rules_for_core_constraints(unsat_core, extended_program)
        return unsat_core

    # Backwards-compatible alias used by the unsat debugging path.
    def compute_minimal_core_unsat(self, extended_program: str) -> UnsatisfiableCore:
        return self.compute_minimal_core(extended_program, None, False, expand_constraints=True)

    def _minimal_core(self, extended_program: str) -> list[str]:
        """Shrink ``self.debug_atoms`` to a minimal incoherent core.

        First a prefix of the guards is grown geometrically until it is
        already incoherent; then a linear deletion pass keeps only the guards
        whose removal restores coherence."""
        if not self.debug_atoms:
            return []
        small_core: list[str] = []
        value = 1
        while self.check_coherence(extended_program, small_core):
            small_core = self.debug_atoms[:value]
            value *= 2
            if len(small_core) == len(self.debug_atoms) and self.check_coherence(extended_program, small_core):
                return []  # coherent even with all guards: no core exists

        core = list(small_core)
        minimal_core: list[str] = []
        while core:
            tmp = extended_program
            candidate = core.pop()
            for item in core:
                tmp += f":- {item}.\n"
            for item in minimal_core:
                tmp += f":- {item}.\n"
            # Still coherent without `candidate` disabled -> candidate is
            # necessary for the incoherence, keep it in the core.
            if not self.runner.is_incoherent(tmp, "--outf=1", "--keep-facts"):
                minimal_core.append(candidate)
        return minimal_core

    def _add_debug_rules_to_core(
        self,
        unsat_core: UnsatisfiableCore,
        minimal_core: list[str],
        *,
        atom: QueryAtom | None,
    ) -> None:
        """Translate the __debug/__support atoms of the minimal core into user
        facing Responses."""
        # Rules, facts and answer-set literals (types 0, 1 and 2).
        for symbol in minimal_core:
            match = re.search(r'__debug\((".*"),([^3]),([^,]*)\)', symbol)
            if not match:
                continue
            considered = match.group(1).replace('"', "").replace("\\", "")
            type_ = int(match.group(2))
            if atom is not None and type_ == 2:
                # Report the literal with the polarity it has in the answer set.
                if self._restore_quotes(considered).removesuffix(".") in self.derived_atoms:
                    unsat_core.add_rule(considered, type_)
                else:
                    unsat_core.add_rule("not " + considered, type_)
            else:
                unsat_core.add_rule(considered, type_)

        # Aggregate rules (type 3): re-insert the variable values recorded in
        # the guard when a specific atom is being explained.
        for symbol in minimal_core:
            match = re.search(r'__debug\((".*"),3,start.*?end,(.*)\)', symbol)
            if not match:
                continue
            considered = match.group(1).replace('"', "").replace("\\", "")
            if atom is not None:
                tmp_vars = match.group(0).split(",start,", 1)[1].split(",end,", 1)[0].split(",")
                for i in range(0, len(tmp_vars) - 1, 2):
                    ref = tmp_vars[i].replace('"', "")
                    considered = re.sub(r"\b" + re.escape(ref) + r"\b", tmp_vars[i + 1], considered)
            if not any(rule.rule == considered and rule.type == 3 for rule in unsat_core.rules):
                unsat_core.add_rule(considered, 3)

        # Supported literals: report the rules that could have derived them.
        for symbol in minimal_core:
            match = re.search(r'__support\((".*"),(.*),(.*)\)', symbol)
            if not match:
                continue
            if self.debug_rules:
                for head in self.search_head(match.group(1).replace('"', ""), self._current_extended_program):
                    if "#count" in head or "#sum" in head:
                        unsat_core.add_rule(head, 3)
                    elif ":-" not in head and "{" not in head and "|" not in head:
                        unsat_core.add_rule(head, 1)
                    else:
                        unsat_core.add_rule(head, 0)

            if atom is not None and self.debug_answer_set:
                # The support text is ":- [not] p." -- extract the atom and
                # report it with the polarity it has in the answer set.
                raw = match.group(1).replace('"', "").removeprefix(":-").strip()
                display = raw.removeprefix("not ").strip()  # e.g. "c."
                bare = self._restore_quotes(display).removesuffix(".").strip()
                if bare in self.unsupported:
                    unsat_core.add_rule("not " + display, 2)
                    continue
                if bare != atom.atom:
                    if bare in self.derived_atoms:
                        unsat_core.add_rule(display, 2)
                    else:
                        unsat_core.add_rule("not " + display, 2)

    def _add_defining_rules_for_core_constraints(
        self,
        unsat_core: UnsatisfiableCore,
        extended_program: str,
    ) -> None:
        """Complete an unsat core with the rules defining the atoms that the
        core constraints require to be true.

        A constraint such as ``:- not a.`` is in the core because ``a`` cannot
        be made true. That is only understandable when the rules that could
        derive ``a`` (i.e. every rule with ``a`` in the head) are part of the
        explanation as well -- the same role ``__support`` atoms play in the
        literal-explanation flow. Atoms with no defining rule are reported
        explicitly."""
        for response in list(unsat_core.rules):
            rule = response.rule
            if not rule.strip().startswith(":-"):
                continue
            body = rule.strip()[2:].strip().rstrip(".")
            for literal in asp_parser.split_top_level(body):
                if not literal.startswith("not "):
                    continue  # only atoms the constraint forces to be true
                atom_text = literal[len("not ") :].strip()
                heads = self.search_head(f":- not {atom_text}.", extended_program)
                if not heads:
                    self._add_unique_response(unsat_core, f"No rules with {atom_text} in the head", 0)
                for head in heads:
                    if "#count" in head or "#sum" in head:
                        self._add_unique_response(unsat_core, head, 3)
                    elif ":-" not in head and "{" not in head and "|" not in head:
                        self._add_unique_response(unsat_core, head, 1)
                    else:
                        self._add_unique_response(unsat_core, head, 0)

    @staticmethod
    def _add_unique_response(unsat_core: UnsatisfiableCore, rule: str, type_: int) -> None:
        """Add a response unless an identical one is already in the core."""
        if not any(item.rule == rule and item.type == type_ for item in unsat_core.rules):
            unsat_core.add_rule(rule, type_)

    def search_head(self, atom: str, program: str) -> list[str]:
        """Find the rules of the extended program whose head could derive the
        literal frozen by ``atom`` (a ``:- [not] a(...).`` constraint)."""
        rules: list[str] = []
        if ":-" not in atom:
            return rules
        atom = self._restore_quotes(atom)
        atom_body = atom.split(":-", 1)[1]
        head = atom_body.split("(", 1)[0].replace("not", "").replace(".", "").strip()
        arity = self.get_arity(atom_body.split("(", 1)[1] if "(" in atom_body else atom_body)

        for line in program.splitlines():
            if line.startswith("{__debug") or "__support" in line:
                continue
            # Collect the head atoms of the line (plain head, disjunction or
            # choice with several elements).
            if ":-" in line and line.split(":-", 1)[0]:
                head_part = line.split(":-", 1)[0]
                if "|" in head_part:
                    candidates = head_part.split("|")
                elif "{" in head_part:
                    candidates = head_part.split(";")
                else:
                    candidates = [head_part]
            elif "|" in line:
                candidates = line.split("|")
            elif "{" in line:
                candidates = line.split(";")
            else:
                candidates = []

            for candidate in candidates:
                head_text = candidate.split(":", 1)[0]
                tmp_head = head_text.replace("{", "").replace("}", "").split("(", 1)[0].strip()
                tmp_arity = self.get_arity(
                    head_text.split("(", 1)[1] if "(" in head_text else head_text
                )
                cleaned_candidate = candidate.strip().replace("{", "").replace("}", "")
                if ":" not in candidate and atom == ":- not " + cleaned_candidate + ".":
                    self._append_debug_source(line, rules)
                elif (
                    head == tmp_head
                    and arity == tmp_arity
                    and self._head_instance_match(self._atom_args(atom_body), self._atom_args(head_text))
                ):
                    self._append_debug_source(line, rules)
        return rules

    @staticmethod
    def _atom_args(text: str) -> list[str]:
        """Argument terms of the first atom in ``text`` (empty for arity 0)."""
        start = text.find("(")
        if start < 0:
            return []
        end = asp_parser._matching_parenthesis(text, start)
        if end < 0:
            return []
        return asp_parser.split_top_level(text[start + 1 : end])

    @staticmethod
    def _head_instance_match(ground_args: list[str], head_args: list[str]) -> bool:
        """Rough unification test between a ground atom and a rule head:
        a constant argument in the head must equal the corresponding ground
        argument; variables and complex terms match anything. Filters out
        rules such as ``a(2) :- ...`` when explaining ``a(1)``."""
        if len(ground_args) != len(head_args):
            return True  # arity already checked; stay permissive on parse issues
        for ground, head in zip(ground_args, head_args):
            head = head.strip()
            ground = ground.strip()
            if not head or head == "_" or head[0].isupper() or head.startswith("_"):
                continue  # variable: matches anything
            if any(marker in head for marker in ("(", "+", "-", "*", "/", "..", ";")):
                continue  # arithmetic/function/interval term: be permissive
            if head != ground:
                return False
        return True

    def get_arity(self, params: str) -> int:
        """Number of top-level commas in an argument list (quote-aware); two
        atoms match when predicate name and this count are equal."""
        arity = 0
        quote: str | None = None
        for char in params:
            if char in {"'", '"'}:
                quote = None if quote == char else char
            elif quote is None and char == ",":
                arity += 1
        return arity

    def _append_debug_source(self, line: str, rules: list[str]) -> None:
        """Add the original rule text stored in the __debug guard of ``line``."""
        match = re.search(r'__debug\((".*"),(.*),(.*)\)', line)
        if match:
            value = match.group(1).replace('"', "").replace("\\", "")
            if value not in rules:
                rules.append(value)

    # ------------------------------------------------------------------
    # Weak-constraint helpers
    # ------------------------------------------------------------------

    def read_costs(self, line: str) -> list[str]:
        """Parse ``[cost@level,terms]`` of a weak constraint into the list
        ``[discriminant, cost, level]`` used to build aux atoms."""
        cost_block = asp_parser.cost_of(line)
        cost, level_discriminant = cost_block.split("@", 1)
        parts = level_discriminant.split(",", 1)
        level = parts[0].strip()
        discriminant = parts[1].strip() if len(parts) > 1 else "empty"
        return [discriminant, cost.strip(), level]

    def generate_aux(self, aux: list[str]) -> str:
        return f"aux({aux[0]},{aux[1]},{aux[2]})"

    def generate_body(self, line: str) -> str:
        return asp_parser.body_of(line)

    def update_cost(self) -> None:
        """Sum the aux atoms of the model into a per-level cost. Levels
        declared in the program start at 0 so they stay inspectable even when
        the optimal answer set pays nothing at that level."""
        self.level_to_cost = {level: 0 for level in self.declared_levels}
        for atom in self.derived_atoms:
            if atom.startswith("aux("):
                tmp_values = atom.split(",")
                if len(tmp_values) >= 3 and tmp_values[-2].lstrip("-").isdigit():
                    tmp_cost = tmp_values[-2]
                    level = tmp_values[-1].replace(")", "")
                    self.level_to_cost[level] = self.level_to_cost.get(level, 0) + int(tmp_cost)

    # ------------------------------------------------------------------
    # Aggregate expansion (the "expand to see why" feature of the UI)
    # ------------------------------------------------------------------

    def generate_set(self, aggregate: str) -> dict[str, dict[str, list[str]]]:
        """Ground the aggregate rule against the atoms of the inspected answer
        set and return, for every instance of the rule, the aggregate elements
        annotated with their truth value.

        The returned mapping is ``instance-key -> element-id -> [atom texts]``.
        When the aggregate has a </> guard, a reduced mapping containing only
        the atoms relevant to satisfy/violate the guard is returned instead.
        """
        total_set: dict[str, dict[str, list[str]]] = {}
        opt_set: dict[str, dict[str, list[str]]] = {}
        # `aggregate` comes from a __debug string: restore double quotes so
        # the rule is parseable again.
        rule_text = self._restore_quotes(aggregate)
        # Aggregate atom as written in the rule (e.g. "DUR = #sum{...}");
        # shown in the instance key so the user sees which aggregate the
        # expansion refers to.
        agg_expression = asp_parser.aggregate_expression(rule_text)
        builder = [rule_text]
        for section in (self.initial_facts, self.derived_atoms, self.false_atoms):
            for rule in section:
                if rule not in self.unsupported_false:  # those can be non-ground
                    builder.append(f"#external {rule}.")

        output = self.runner.ground_text("\n".join(builder) + "\n")
        pattern = re.compile(r"\{(.*?)\}")
        to_delete = ""
        created: dict[str, str] = {}
        temp_map: dict[str, str] = {}

        for grounded_atom in output.splitlines():
            if grounded_atom.startswith("#external"):
                continue
            if grounded_atom.startswith("#"):
                # gringo may abbreviate bodies via auxiliary "#x :- body"
                # definitions: remember them so they can be inlined below.
                if ":-" in grounded_atom and grounded_atom.split(":-", 1)[0] and grounded_atom.split(":-", 1)[1]:
                    created[grounded_atom.split(":-", 1)[0].strip()] = grounded_atom.split(":-", 1)[1].strip()
                continue

            matcher = pattern.search(grounded_atom)
            if not matcher:
                continue
            if ":-" in grounded_atom:
                tmp_outside = grounded_atom.split(":-", 1)[1]
            elif "<=>" in grounded_atom:
                tmp_outside = grounded_atom.split("<=>", 1)[1]
            else:
                continue

            for key, value in created.items():
                if key in tmp_outside:
                    tmp_outside = tmp_outside.replace(key, value[:-1])

            # Strip the aggregate itself, keeping the rest of the body: it
            # identifies the rule instance this aggregate belongs to.
            outside = []
            copy = True
            for char in tmp_outside[:-1]:
                if char == "#":
                    copy = False
                if char == "}":
                    copy = True
                    continue
                if copy:
                    outside.append(char)
            outside_text = "".join(outside)

            # Separate the comparison/assignment block of the aggregate
            # (e.g. "> 1" or "4 =") from the rest of the body: the remaining
            # literals identify this ground instance of the rule and are used
            # as the key shown in the UI.
            blocks = asp_parser.split_top_level(outside_text)
            guard_block = ""
            for block in blocks:
                if any(operator in block for operator in ["=", "<", ">"]):
                    guard_block = block
            if guard_block:
                to_delete = guard_block

            temp = matcher.group()[1:-1]
            # Instance key: the body literals outside the aggregate plus the
            # aggregate expression with the grounded guard value filled in
            # (e.g. 'reg("pat1","bed"), 2 = #sum{...}').
            key_parts = [block for block in blocks if block != guard_block]
            instantiated = self._instantiated_aggregate(agg_expression, guard_block)
            if instantiated:
                key_parts.append(instantiated)
            new_key = ", ".join(key_parts)
            internal = self.get_id_set(temp, new_key, total_set)

            if "#count" in aggregate or "#sum" in aggregate:
                is_count = "#count" in aggregate
                entry = total_set.get(new_key, {})
                # Message: truth == "the grounded aggregate holds in the
                # answer set". (The Java version reported #sum inverted,
                # compensating a counting bug that ignored facts and negated
                # literals; both are fixed in check_truth.)
                truth = self.check_truth(entry, to_delete, is_count)
                temp_map[new_key] = (
                    " the aggregate is true, expand to see why"
                    if truth
                    else " the aggregate is false, expand to see why"
                )
                if ">" in to_delete or "<" in to_delete:
                    # Branch selection: the tables in inspect_count/inspect_sum
                    # were tuned against the original (derived-atoms-only)
                    # truth computation, so that legacy value is kept for them.
                    inspect = self.inspect_count if is_count else self.inspect_sum
                    legacy = self._legacy_truth(entry, to_delete, is_count)
                    inspect(opt_set, new_key, entry, to_delete, internal, legacy)

        self.truth_aggregate[aggregate] = temp_map
        self.set_false_true(total_set)
        return opt_set if (">" in to_delete or "<" in to_delete) else total_set

    def get_truth_aggregate(self, rule: str, key: str) -> str:
        """Truth message computed by the last generate_set call for ``rule``."""
        return self.truth_aggregate.get(rule, {}).get(key, "")

    def get_id_set(
        self,
        body: str,
        new_key: str,
        total_set: dict[str, dict[str, list[str]]],
    ) -> bool:
        """Split the grounded aggregate body ``id1:atom1;id2:atom2;...`` into
        a per-id mapping stored in ``total_set``. Returns True when the atom
        under analysis appears among the elements ("internal" occurrence)."""
        set_values: dict[str, list[str]] = OrderedDict()
        found = False
        for block in body.split(";"):
            if ":" not in block:
                continue
            temp_id, temp_body = block.split(":", 1)
            if not found and self.analyzed is not None and self.analyzed.atom in temp_body:
                found = True
            set_values.setdefault(temp_id, []).append(temp_body)
        total_set[new_key] = set_values
        return found

    def set_false_true(self, values: dict[str, dict[str, list[str]]]) -> None:
        """Annotate every aggregate element with its truth value in the
        inspected answer set. Single negated literals are shown as the truth
        of the underlying atom; multi-literal conditions (``p(X), not q(X)``)
        are annotated as a whole conjunction."""
        for key_global, mapping in values.items():
            for key_local, atoms in mapping.items():
                for index, atom in enumerate(list(atoms)):
                    literals = asp_parser.split_top_level(atom)
                    if len(literals) > 1:
                        truth = " is true" if self._is_true_condition(atom) else " is false"
                        values[key_global][key_local][index] = atom + truth
                        continue
                    bare = atom.removeprefix("not ")
                    if atom.startswith("not "):
                        truth = " is false" if bare in self.false_atoms else " is true"
                    else:
                        truth = " is true" if self._is_true_element(atom) else " is false"
                    values[key_global][key_local][index] = bare + truth

    def _is_true_condition(self, condition: str) -> bool:
        """Truth of an aggregate element condition, which may be a comma
        separated conjunction of literals (``p(1),not q(1)``)."""
        literals = [literal.strip() for literal in asp_parser.split_top_level(condition) if literal.strip()]
        if not literals:
            return False
        return all(self._is_true_element(literal) for literal in literals)

    def _is_true_element(self, atom: str) -> bool:
        """Truth of one aggregate element text in the inspected answer set.
        (Prefix handling, not substring: atoms such as p("not ok") must not
        be treated as negated literals.)"""
        if atom.startswith("not "):
            return atom.removeprefix("not ") in self.false_atoms
        return atom in self.derived_atoms or atom in self.initial_facts

    # The four find_*_agg_until* helpers walk the solver assignment order and
    # return the first atoms that satisfy (true variants) or violate (false
    # variants) the aggregate guard. The *_sum variants weigh every element by
    # the first term of its id (its #sum weight) instead of counting 1.

    def find_true_agg_until(
        self,
        entry: dict[str, list[str]],
        value_guard: int,
        slack: int,
        less: bool,
    ) -> dict[str, list[str]]:
        used_groups: set[str] = set()
        result: dict[str, list[str]] = OrderedDict()
        counter = 0
        # Elements never touched by the solver order are checked last.
        last_set: list[tuple[str, str]] = []
        for key, values in entry.items():
            for atom in values:
                if atom not in self.order and atom.removeprefix("not ") not in self.order and ("not " + atom) not in self.order:
                    last_set.append((key, atom))

        for check_atom in self.order:
            if not ((less and counter <= value_guard) or (not less and counter < value_guard + slack)):
                return result
            for key, values in entry.items():
                if key in used_groups:
                    continue
                if check_atom in values:
                    used_groups.add(key)
                    result[key] = [check_atom + " is true"]
                    counter += 1
                    break

        for key, check_atom in last_set:
            if not ((less and counter <= value_guard) or (not less and counter < value_guard + slack)):
                return result
            if key in used_groups:
                continue
            if self._is_true_element(check_atom):
                used_groups.add(key)
                result[key] = [check_atom + " is true"]
                counter += 1
        return result

    def find_true_agg_until_sum(
        self,
        entry: dict[str, list[str]],
        value_guard: int,
        slack: int,
        less: bool,
    ) -> dict[str, list[str]]:
        avoid: list[str] = []
        result: dict[str, list[str]] = OrderedDict()
        counter = 0
        last_set: list[list[str]] = []
        for key, values in entry.items():
            for atom in values:
                if atom not in self.order and atom.removeprefix("not ") not in self.order and ("not " + atom) not in self.order:
                    last_set.append([key, atom])

        for check_atom in self.order:
            if not ((less and counter <= value_guard) or (not less and counter < value_guard + slack)):
                return result
            for key, values in entry.items():
                if key in avoid:
                    break
                if check_atom in values:
                    avoid.append(key)
                    result[key] = [check_atom + " is true"]
                    counter += int(key.split(",")[0])
                    break

        for key_atom in last_set:
            check_atom = key_atom[1]
            if not ((less and counter <= value_guard) or (not less and counter < value_guard + slack)):
                return result
            if key_atom[0] in avoid:
                break
            if self._is_true_element(check_atom):
                avoid.append(key_atom[0])
                result[key_atom[0]] = [check_atom + " is true"]
                counter += int(key_atom[0].split(",")[0])
                break
        return result

    def find_false_agg_until(
        self,
        entry: dict[str, list[str]],
        value_guard: int,
        slack: int,
        less: bool,
    ) -> dict[str, list[str]]:
        result: dict[str, list[str]] = OrderedDict()
        counter = 0
        total_atoms = sum(len(values) for values in entry.values())
        last_set: list[list[str]] = []
        for key, values in entry.items():
            for atom in values:
                if atom not in self.order and atom.removeprefix("not ") not in self.order and ("not " + atom) not in self.order:
                    last_set.append([key, atom])

        for check_atom in self.order:
            if not ((not less and counter < total_atoms - (value_guard - slack)) or (less and counter <= total_atoms - value_guard)):
                return result
            for key, values in entry.items():
                # The order lists true atoms: the complementary literal being
                # among the elements means the element is false.
                temp_atom = check_atom.removeprefix("not ") if check_atom.startswith("not ") else "not " + check_atom
                if temp_atom in values:
                    result.setdefault(key, []).append(check_atom + " is false ")
                    counter += 1

        for key_atom in last_set:
            check_atom = key_atom[1]
            if not ((not less and counter < total_atoms - (value_guard - slack)) or (less and counter <= total_atoms - value_guard)):
                return result
            # "not a" is false when a is derived/fact; "a" is false when a is
            # among the grounded-but-absent atoms.
            if (
                (check_atom.startswith("not ") and (check_atom.removeprefix("not ") in self.derived_atoms or check_atom.removeprefix("not ") in self.initial_facts))
                or (not check_atom.startswith("not ") and check_atom in self.false_atoms)
            ):
                result.setdefault(key_atom[0], []).append(check_atom + " is false ")
                counter += 1
        return result

    def find_false_agg_until_sum(
        self,
        entry: dict[str, list[str]],
        value_guard: int,
        slack: int,
        less: bool,
    ) -> dict[str, list[str]]:
        result: dict[str, list[str]] = OrderedDict()
        counter = 0
        total_atoms = sum(len(values) for values in entry.values())
        for check_atom in self.order:
            if not ((not less and counter < total_atoms - (value_guard - slack)) or (less and counter <= total_atoms - value_guard)):
                return result
            for key, values in entry.items():
                temp_atom = check_atom.removeprefix("not ") if check_atom.startswith("not ") else "not " + check_atom
                if temp_atom in values:
                    result.setdefault(key, []).append(check_atom + " is false")
                    counter += int(key.split(",", 1)[0])
                    break
        return result

    def find_true_agg_all(self, entry: dict[str, list[str]], _sum_agg: bool) -> dict[str, list[str]]:
        """All true elements of the aggregate."""
        result: dict[str, list[str]] = OrderedDict()
        for key, values in entry.items():
            temp: list[str] = []
            for atom in values:
                if self._is_true_element(atom):
                    temp.append(atom.removeprefix("not ") + " is true ")
            if temp:
                result[str(key)] = temp
        return result

    def find_false_agg_all(self, entry: dict[str, list[str]], _sum_agg: bool) -> dict[str, list[str]]:
        """All false elements of the aggregate."""
        result: dict[str, list[str]] = OrderedDict()
        for key, values in entry.items():
            temp: list[str] = []
            for atom in values:
                if not self._is_true_element(atom):
                    temp.append(atom.removeprefix("not ") + " is false ")
            if temp:
                result[str(key)] = temp
        return result

    def inspect_count(
        self,
        opt_set: dict[str, dict[str, list[str]]],
        key: str,
        entry: dict[str, list[str]],
        guard: str,
        internal: bool,
        truth: bool,
    ) -> str:
        """Select which #count elements to show, depending on the guard
        direction, on whether the aggregate holds (``truth``) and on whether
        the analyzed atom occurs inside the aggregate (``internal``).

        Note: gringo shows aggregates with inverted comparison signs, hence
        the seemingly swapped branches."""
        value_guard = self._first_int(guard)
        if truth:
            if internal:
                if "<=" in guard:
                    opt_set[key] = self.find_true_agg_until(entry, value_guard, 0, False)
                    return f"Showing the first {value_guard} true atoms "
                if "<" in guard:
                    opt_set[key] = self.find_true_agg_until(entry, value_guard, 0, True)
                    return f"Showing the first {value_guard} - 1 true atoms "
                if ">=" in guard:
                    opt_set[key] = self.find_false_agg_until(entry, value_guard, 0, False)
                    return f"Showing the first {value_guard} false atoms "
                if ">" in guard:
                    opt_set[key] = self.find_false_agg_until(entry, value_guard, 0, True)
                    return f"Showing the first {value_guard} - 1 false atoms "
            else:
                if "<=" in guard:
                    opt_set[key] = self.find_false_agg_until(entry, value_guard, 1, False)
                    return f"Showing the first {value_guard} false atoms causing conflict "
                if "<" in guard:
                    opt_set[key] = self.find_false_agg_until(entry, value_guard, 0, False)
                    return f"Showing the first {value_guard} false atoms causing conflict "
                if ">=" in guard:
                    opt_set[key] = self.find_true_agg_until(entry, value_guard, -1, False)
                    return f"Showing the first {value_guard} true atoms causing conflict "
                if ">" in guard:
                    opt_set[key] = self.find_true_agg_until(entry, value_guard, 0, False)
                    return f"Showing all the first {value_guard} true atoms causing conflict "
        else:
            if internal:
                if "<" in guard:
                    opt_set[key] = self.find_false_agg_all(entry, False)
                    return "Showing all the false atoms "
                if ">" in guard:
                    opt_set[key] = self.find_true_agg_all(entry, False)
                    return "Showing all the positive atoms "
            else:
                if "<" in guard:
                    opt_set[key] = self.find_true_agg_all(entry, False)
                    return "Showing all the positive atoms "
                if ">" in guard:
                    opt_set[key] = self.find_false_agg_all(entry, False)
                    return "Showing all the false atoms "
        return ""

    def inspect_sum(
        self,
        opt_set: dict[str, dict[str, list[str]]],
        key: str,
        entry: dict[str, list[str]],
        guard: str,
        internal: bool,
        truth: bool,
    ) -> str:
        """Same as inspect_count but for #sum (elements are weighted)."""
        value_guard = self._first_int(guard)
        if truth:
            if internal:
                if "<=" in guard:
                    opt_set[key] = self.find_true_agg_until_sum(entry, value_guard, 0, False)
                    return "Showing the first true atoms "
                if "<" in guard:
                    opt_set[key] = self.find_true_agg_until_sum(entry, value_guard, 0, True)
                    return "Showing the first true atoms "
                if ">=" in guard:
                    opt_set[key] = self.find_false_agg_until_sum(entry, value_guard, 0, False)
                    return "Showing the first false atoms satisfying the aggregate "
                if ">" in guard:
                    opt_set[key] = self.find_false_agg_until_sum(entry, value_guard, 0, True)
                    return "Showing the first false atoms satisfying the aggregate "
            else:
                if "<=" in guard:
                    opt_set[key] = self.find_false_agg_until_sum(entry, value_guard, -1, False)
                    return "Showing the first false atoms causing conflict "
                if "<" in guard:
                    opt_set[key] = self.find_false_agg_until_sum(entry, value_guard, 0, False)
                    return "Showing the first false atoms causing conflict "
                if ">=" in guard:
                    opt_set[key] = self.find_true_agg_until_sum(entry, value_guard, 1, False)
                    return "Showing the first true atoms causing conflict "
                if ">" in guard:
                    opt_set[key] = self.find_true_agg_until_sum(entry, value_guard, 0, False)
                    return "Showing the first atoms causing conflict "
        else:
            if internal:
                if "<" in guard:
                    opt_set[key] = self.find_false_agg_all(entry, True)
                    return "Showing all the false atoms "
                if ">" in guard:
                    opt_set[key] = self.find_true_agg_all(entry, True)
                    return "Showing all the true atoms "
            else:
                if "<" in guard:
                    opt_set[key] = self.find_true_agg_all(entry, True)
                    return "Showing all the true atoms "
                if ">" in guard:
                    opt_set[key] = self.find_false_agg_all(entry, True)
                    return "Showing all the false atoms "
        return ""

    def check_truth(self, mapping: dict[str, list[str]], guard: str, count: bool) -> bool:
        """Evaluate the aggregate over the inspected answer set and compare
        the result with the (sign-inverted, see inspect_count) guard.

        An element counts (with weight 1 for #count, with the first term of
        its id for #sum) when at least one of its recorded conditions holds;
        conditions may be conjunctions and may contain negated literals."""
        counter = 0
        for key, values in mapping.items():
            if not any(self._is_true_condition(condition) for condition in values):
                continue
            if count:
                counter += 1
            else:
                weight = key.split(",")[0].strip()
                counter += int(weight) if weight.lstrip("-").isdigit() else 0
        return self._compare_guard(counter, guard)

    def _legacy_truth(self, mapping: dict[str, list[str]], guard: str, count: bool) -> bool:
        """Truth as computed by the original implementation: only derived
        atoms count (facts and negated literals are ignored). Wrong as a truth
        value, but the branch tables of inspect_count/inspect_sum were built
        around it, so it is preserved for selecting which atoms to display."""
        counter = 0
        for key, values in mapping.items():
            if not any(value in self.derived_atoms for value in values):
                continue
            if count:
                counter += 1
            else:
                weight = key.split(",")[0].strip()
                counter += int(weight) if weight.lstrip("-").isdigit() else 0
        return self._compare_guard(counter, guard)

    def _compare_guard(self, counter: int, guard: str) -> bool:
        """Compare a computed aggregate value with the printed guard
        (value on the left, e.g. ``1<`` means "aggregate > 1")."""
        value_guard = self._first_int(guard)
        if ">" in guard:
            return counter <= value_guard if "=" in guard else counter < value_guard
        if "<" in guard:
            return counter >= value_guard if "=" in guard else counter > value_guard
        if "=" in guard:
            return counter != value_guard if "!=" in guard else counter == value_guard
        return True

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _populate_atoms_from_witness(self, witness: list[str], grounded: list[str]) -> None:
        """Derive ``derived_atoms``/``false_atoms`` from a model: derived are
        the model atoms that are not facts, false are the grounded heads that
        are neither derived nor facts."""
        self.derived_atoms = []
        self.false_atoms = []
        for atom in witness:
            if atom.startswith("__debug") or atom == "":
                continue
            if atom not in self.initial_facts:
                self._add_unique(self.derived_atoms, atom)
        for ground in grounded:
            if (
                ground
                and ground not in self.derived_atoms
                and ground not in self.initial_facts
                and ground not in self.false_atoms
            ):
                self.false_atoms.append(ground)
        # Atoms with no defining rule never appear among grounded heads but
        # are certainly false: keep them visible for inspection.
        for atom in self.unsupported_false:
            if atom not in self.derived_atoms and atom not in self.initial_facts:
                self._add_unique(self.false_atoms, atom)
        if self.optimization_problem:
            self.update_cost()

    def _register_unsupported(self, atoms: list[str], program: str) -> None:
        """Record atoms reported by gringo as never occurring in a rule head,
        together with the heads of rules that positively depend on them (those
        heads are also false in every answer set)."""
        for atom in atoms:
            self._add_unique(self.unsupported, atom)
            self._add_unique(self.unsupported_false, atom)

        for line in program.splitlines():
            if ":-" not in line:
                continue
            head, _, body = line.partition(":-")
            head = head.strip()
            if not head or head in self.unsupported_false or head in self.initial_facts:
                continue
            for atom in atoms:
                if re.search(r"(?<=[\s,(:])" + re.escape(atom) + r"(?=[\s,.)])", " " + body):
                    self._add_unique(self.unsupported_false, head)
                    break

    @staticmethod
    def _instantiated_aggregate(agg_expression: str, guard_block: str) -> str:
        """Combine the aggregate expression written in the rule with the
        guard value found in one ground instance.

        For an assignment aggregate the variable is replaced by its value:
        ``DUR = #sum{...}`` + grounded guard ``2=`` -> ``2 = #sum{...}``.
        For constant guards the original expression is kept as written."""
        if not agg_expression:
            return ""
        if not guard_block:
            return agg_expression
        # gringo prints the guard either as "<value><op>" or "<op><value>".
        match = re.match(r"^\s*([^\s<>!=]+)\s*(?:!=|<=|>=|=|<|>)\s*$", guard_block)
        if not match:
            match = re.match(r"^\s*(?:!=|<=|>=|=|<|>)\s*([^\s<>!=]+)\s*$", guard_block)
        if not match:
            return agg_expression
        value = match.group(1)
        # Replace a variable guard (e.g. DUR) with the grounded value.
        guard_var = re.match(r"^([A-Z][A-Za-z0-9_]*)\s*(?:!=|<=|>=|=|<|>)\s*#", agg_expression)
        if guard_var:
            return value + agg_expression[len(guard_var.group(1)) :]
        return agg_expression

    @staticmethod
    def _restore_quotes(text: str) -> str:
        """Undo the double->single quote replacement applied when embedding
        rule text into __debug/__support string constants."""
        return text.replace("'", '"')

    @staticmethod
    def _add_unique(values: list[str], item: str) -> None:
        if item not in values:
            values.append(item)

    @staticmethod
    def _first_int(text: str) -> int:
        match = re.search(r"-?\d+", text)
        return int(match.group(0)) if match else 0
