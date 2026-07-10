"""Thin wrapper around clingo used by the debugger.

Three operations are exposed:

- ``solve``: run the solver through the clingo Python API. Besides the
  models, it records for every model the *derivation order* of the atoms
  (which atom became true before which, via a propagator) and the set of all
  grounded head atoms. Both are needed by the debugger to decide which atoms
  are false and in which order aggregate elements should be presented.
- ``is_incoherent``: quick satisfiability check used while minimizing the
  unsatisfiable core.
- ``ground_text``: run ``gringo --text`` (through ``python -m clingo``) and
  return the grounded program as text. Used to enumerate ground instances of
  rules and aggregates.

This replaces the Java implementation that shelled out to a clingo binary
plus the ``helper.lp`` script with the embedded Python propagator.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import Settings


# Marker emitted by gringo when an atom appears only in rule bodies.
_UNSUPPORTED_MARKER = "atom does not occur in any rule head"


class ClingoError(RuntimeError):
    """Raised when clingo is missing or reports an error."""


@dataclass
class SolveSummary:
    """Result of one ``solve`` call."""

    satisfiable: bool
    unsatisfiable: bool
    unknown: bool
    #: One list of shown atoms per computed model.
    witnesses: list[list[str]] = field(default_factory=list)
    #: All grounded head atoms (used to derive the false atoms of a model).
    heads: list[str] = field(default_factory=list)
    #: For each model, the order in which atoms were assigned by the solver.
    orders: list[list[str]] = field(default_factory=list)
    #: Atoms reported by gringo as never occurring in a rule head.
    unsupported_atoms: list[str] = field(default_factory=list)


class ClingoRunner:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings.load()

    def solve(
        self,
        program: str,
        *,
        models: int = 1,
        optimization: bool = False,
        check_error: bool = False,
    ) -> SolveSummary:
        """Solve ``program`` asking for ``models`` answer sets.

        ``check_error`` mirrors the Java API: when True, gringo info messages
        about unsupported atoms are collected in the summary.
        """
        del check_error  # info messages are always collected
        try:
            return self._solve_with_api(program, models=models, optimization=optimization)
        except ModuleNotFoundError as exc:
            raise ClingoError("Install the `clingo` Python package with pip before running E-ASP.") from exc

    def is_incoherent(self, encoding: str, *options: str) -> bool:
        """True when ``encoding`` has no answer set."""
        del options  # kept for signature compatibility with the CLI version
        try:
            return self._is_incoherent_with_api(encoding)
        except ModuleNotFoundError as exc:
            raise ClingoError("Install the `clingo` Python package with pip before running E-ASP.") from exc

    def ground_text(self, encoding: str) -> str:
        """Ground ``encoding`` and return gringo's textual output."""
        result = self._run_cli(encoding, ["--mode=gringo", "--text"])
        # 0/10/20/30 are regular clingo exit codes (sat/unsat/interrupted...).
        if result.returncode not in {0, 10, 20, 30} and result.stderr.strip():
            raise ClingoError(result.stderr.strip())
        return result.stdout

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _solve_with_api(self, program: str, *, models: int, optimization: bool) -> SolveSummary:
        import clingo

        options = [str(max(0, models))]
        if optimization:
            # optN: after the optimum is found, optimal models are
            # re-enumerated with optimality_proven set. The CLI would use
            # --quiet to hide the intermediate models, but libclingo does not
            # accept that option, so they are filtered in the loop below and
            # the model limit is enforced manually.
            options = ["--opt-mode=optN", "0"]

        unsupported: list[str] = []

        def logger(_code: Any, message: str) -> None:
            # gringo reports e.g. "...: info: atom does not occur in any rule
            # head:\n  b(1)" -- the atom is on the line after the marker.
            if _UNSUPPORTED_MARKER in message:
                tail = message.split(_UNSUPPORTED_MARKER, 1)[1].lstrip(":\n ")
                atom = tail.splitlines()[0].strip() if tail.strip() else ""
                if atom and atom not in unsupported:
                    unsupported.append(atom)

        observer = _HeadObserver()
        propagator = _OrderPropagator(observer.head_literals)
        ctl = clingo.Control(options, logger=logger, message_limit=1000)
        ctl.register_observer(observer)
        ctl.register_propagator(propagator)
        ctl.add("base", [], program)
        ctl.ground([("base", [])])

        witnesses: list[list[str]] = []
        stopped_early = False
        with ctl.solve(yield_=True) as handle:
            for model in handle:
                if optimization and not model.optimality_proven:
                    continue  # intermediate model found while optimizing
                witnesses.append([str(symbol) for symbol in model.symbols(shown=True)])
                # Snapshot the assignment order reached for this model.
                propagator.final_orders.append(list(propagator.order))
                if optimization and models > 0 and len(witnesses) >= models:
                    stopped_early = True
                    break
            if stopped_early:
                # Enough optimal models collected: the search is cancelled by
                # the context manager, but the outcome is known.
                satisfiable, unsatisfiable, unknown = True, False, False
            else:
                result = handle.get()
                satisfiable = bool(result.satisfiable)
                unsatisfiable = bool(result.unsatisfiable)
                unknown = bool(result.unknown)

        return SolveSummary(
            satisfiable=satisfiable,
            unsatisfiable=unsatisfiable,
            unknown=unknown,
            witnesses=witnesses,
            heads=list(dict.fromkeys(propagator.heads)),
            orders=[_deduplicate(order) for order in propagator.final_orders],
            unsupported_atoms=unsupported,
        )

    def _is_incoherent_with_api(self, encoding: str) -> bool:
        import clingo

        # message_limit=0 silences warnings about unsupported atoms, which are
        # expected in the instrumented programs.
        ctl = clingo.Control(["--keep-facts"], logger=lambda code, msg: None, message_limit=0)
        ctl.add("base", [], encoding)
        ctl.ground([("base", [])])
        return bool(ctl.solve().unsatisfiable)

    def _run_cli(self, encoding: str, options: list[str]) -> subprocess.CompletedProcess[str]:
        """Run ``python -m clingo`` on a temporary file (only needed for
        ``--text`` output, which the API does not provide)."""
        temp_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile("w", suffix=".asp", prefix="e-asp-", delete=False) as handle:
                handle.write(encoding)
                temp_path = handle.name
            command = [sys.executable, "-m", "clingo", temp_path, *[option.strip() for option in options]]
            return subprocess.run(
                command,
                check=False,
                capture_output=True,
                cwd=os.getcwd(),
                text=True,
            )
        finally:
            if temp_path:
                Path(temp_path).unlink(missing_ok=True)


class _HeadObserver:
    """Grounding observer that records the literal of every rule head, so the
    propagator can restrict its watches to head atoms."""

    def __init__(self) -> None:
        self.head_literals: list[int] = []

    def rule(self, choice: bool, head: list[int], body: list[int]) -> None:
        del choice, body
        self.head_literals.extend(head)


class _OrderPropagator:
    """Propagator that tracks the order in which head atoms become true.

    The debugger uses this order to answer questions such as "which atoms
    made the aggregate reach its bound first".
    """

    def __init__(self, head_literals: list[int]) -> None:
        # Shared with _HeadObserver: filled during grounding, read in init().
        self.head_literals = head_literals
        self.atom_to_symbol: dict[int, list[Any]] = {}
        self.order: list[str] = []
        self.heads: list[str] = []
        self.final_orders: list[list[str]] = []

    def init(self, init: Any) -> None:
        for atom in init.symbolic_atoms:
            self.heads.append(str(atom.symbol))
            if atom.literal not in self.head_literals:
                continue
            solver_literal = init.solver_literal(atom.literal)
            self.atom_to_symbol.setdefault(solver_literal, []).append(atom.symbol)
            init.add_watch(solver_literal)
            # Atoms fixed at grounding time (facts) come first in the order.
            if init.assignment.is_fixed(solver_literal) and init.assignment.is_true(solver_literal):
                self.order.append(str(atom.symbol))

    def propagate(self, ctl: Any, changes: list[int]) -> None:
        del ctl
        for literal in changes:
            for symbol in self.atom_to_symbol.get(literal, []):
                negative = f"not {symbol}"
                if negative in self.order:
                    self.order.remove(negative)
                self.order.append(str(symbol))

    def decide(self, thread_id: int, assignment: Any, fallback: int) -> int:
        del thread_id, assignment
        # ``fallback`` is a signed solver literal; the mapping key is its
        # absolute value. A decision on the literal means the solver is about
        # to guess it, so record the (tentative) "not atom" entry.
        for symbol in self.atom_to_symbol.get(abs(fallback), []):
            atom_text = str(symbol)
            if atom_text in self.order:
                self.order.remove(atom_text)
            self.order.append(f"not {atom_text}")
        return fallback

    def undo(self, thread_id: int, assignment: Any, changes: list[int]) -> None:
        del thread_id, assignment
        for literal in changes:
            for symbol in self.atom_to_symbol.get(literal, []):
                symbol_text = str(symbol)
                if symbol_text in self.order:
                    self.order.remove(symbol_text)


def _deduplicate(order: list[str]) -> list[str]:
    """Remove duplicates from an order list, keeping first occurrences."""
    return list(dict.fromkeys(order))
