from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest import TestCase

from easp import asp_parser
from easp.models import QueryAtom, Response
from easp.services import (
    ComputeAnswerSetsService,
    DebugProgramService,
    ExplainAtomService,
    Justifier,
    RetrieveAtomsService,
)


CASE_DIR = Path(__file__).parent / "fixtures" / "debugger_cases"

RESPONSE_TYPES = {
    "rule": 0,
    "fact": 1,
    "literal": 2,
    "aggregate": 3,
}

ATOM_VALUES = {
    "false": QueryAtom.FALSE,
    "true": QueryAtom.TRUE,
    "undefined": QueryAtom.UNDEFINED,
    "not_set": QueryAtom.NOT_SET,
}


@dataclass(frozen=True)
class DebuggerCase:
    path: Path
    data: dict[str, Any]

    @property
    def name(self) -> str:
        return str(self.data.get("name") or self.path.stem)

    @property
    def program(self) -> str:
        program = self.data.get("program", "")
        if not isinstance(program, str) or not program.strip():
            raise AssertionError(f"{self.path}: field 'program' must contain ASP source.")
        return program

    @property
    def debug_rules(self) -> bool:
        return bool(self.data.get("debug_rules", True))

    @property
    def debug_answer_set(self) -> bool:
        return bool(self.data.get("debug_answer_set", True))

    @property
    def answer_set_count(self) -> int:
        return int(self.data.get("answer_set_count", 1))


def load_cases(case_dir: Path = CASE_DIR) -> list[DebuggerCase]:
    if not case_dir.exists():
        return []

    cases: list[DebuggerCase] = []
    for path in sorted(case_dir.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("skip"):
            continue
        cases.append(DebuggerCase(path=path, data=data))
    return cases


def assert_answer_sets(test: TestCase, case: DebuggerCase) -> None:
    expected = case.data.get("expected_answer_sets")
    expects_unsat = case.data.get("satisfiable") is False
    if expected is None and not expects_unsat:
        test.skipTest(f"{case.name}: no expected_answer_sets configured.")

    justifier = _new_justifier(case)
    answer_sets = ComputeAnswerSetsService(justifier, case.answer_set_count).run()

    if expects_unsat:
        test.assertIsNone(answer_sets)
        return

    test.assertIsNotNone(answer_sets)
    actual_sets = [_normalize_answer_set(answer_set) for answer_set in answer_sets or []]
    expected_sets = [_normalize_answer_set(answer_set) for answer_set in expected]
    _assert_sequence(test, actual_sets, expected_sets, ordered=bool(case.data.get("answer_sets_ordered", False)))


def assert_atoms(test: TestCase, case: DebuggerCase) -> None:
    expected = case.data.get("expected_atoms")
    if expected is None:
        test.skipTest(f"{case.name}: no expected_atoms configured.")

    justifier = _new_justifier(case)
    answer_sets = ComputeAnswerSetsService(justifier, case.answer_set_count).run()
    if case.data.get("satisfiable") is False:
        test.assertIsNone(answer_sets)
        DebugProgramService(justifier).run()
        atoms = []
    else:
        test.assertIsNotNone(answer_sets, f"{case.name}: expected a satisfiable program.")
        atoms = RetrieveAtomsService(justifier, int(case.data.get("answer_set_index", 0))).run()

    actual = [_atom_to_record(atom) for atom in atoms]
    expected_records = [_expected_atom_to_record(atom) for atom in expected]
    for expected_record in expected_records:
        test.assertTrue(
            any(_record_matches(actual_record, expected_record) for actual_record in actual),
            f"{case.name}: expected atom not found: {expected_record}. Actual atoms: {actual}",
        )


def assert_literal_explanations(test: TestCase, case: DebuggerCase) -> None:
    explanations = case.data.get("explanations")
    if not explanations:
        test.skipTest(f"{case.name}: no explanations configured.")

    justifier = _new_justifier(case)
    _compute_answer_sets(test, justifier, case)
    atoms = RetrieveAtomsService(justifier, int(case.data.get("answer_set_index", 0))).run()

    for explanation in explanations:
        literal = str(explanation["literal"])
        query_atom = _find_query_atom(justifier, atoms, literal)
        chain = [
            _find_query_atom(justifier, atoms, str(chain_literal))
            for chain_literal in explanation.get("chain", [])
        ]
        responses = ExplainAtomService(
            justifier,
            chain,
            query_atom,
            bool(explanation.get("check_opt", False)),
        ).run()

        expected = explanation.get("expected_responses")
        if expected is None:
            continue
        actual_responses = [_response_to_record(response) for response in responses]
        expected_responses = [_expected_response_to_record(response) for response in expected]
        _assert_sequence(
            test,
            actual_responses,
            expected_responses,
            ordered=bool(explanation.get("responses_ordered", False)),
        )


def assert_unsat_debug(test: TestCase, case: DebuggerCase) -> None:
    expected = case.data.get("expected_unsat_responses")
    if expected is None:
        test.skipTest(f"{case.name}: no expected_unsat_responses configured.")

    justifier = _new_justifier(case)
    answer_sets = ComputeAnswerSetsService(justifier, case.answer_set_count).run()
    test.assertIsNone(answer_sets)

    responses = DebugProgramService(justifier).run()
    actual_responses = [_response_to_record(response) for response in responses]
    expected_responses = [_expected_response_to_record(response) for response in expected]
    _assert_sequence(
        test,
        actual_responses,
        expected_responses,
        ordered=bool(case.data.get("unsat_responses_ordered", False)),
    )


def assert_aggregate_expansions(test: TestCase, case: DebuggerCase) -> None:
    expected_expansions = case.data.get("aggregate_expansions")
    if not expected_expansions:
        test.skipTest(f"{case.name}: no aggregate_expansions configured.")

    justifier = _new_justifier(case)
    _compute_answer_sets(test, justifier, case)
    atoms = RetrieveAtomsService(justifier, int(case.data.get("answer_set_index", 0))).run()

    for expected_expansion in expected_expansions:
        literal = expected_expansion.get("literal")
        if literal:
            query_atom = _find_query_atom(justifier, atoms, str(literal))
            ExplainAtomService(justifier, [], query_atom, bool(expected_expansion.get("check_opt", False))).run()

        rule = str(expected_expansion["rule"])
        expanded = justifier.expand_aggregate(rule)
        if "expected" in expected_expansion:
            test.assertEqual(
                _normalize_aggregate_mapping(expanded),
                _normalize_aggregate_mapping(expected_expansion["expected"]),
            )

        expected_truth = expected_expansion.get("expected_truth")
        if expected_truth is not None:
            actual_truth = {key: justifier.truth_aggregate(rule, key) for key in expanded}
            test.assertEqual(
                _normalize_truth_mapping(actual_truth),
                _normalize_truth_mapping(expected_truth),
            )


def _new_justifier(case: DebuggerCase) -> Justifier:
    return Justifier(case.program, case.debug_rules, case.debug_answer_set)


def _compute_answer_sets(test: TestCase, justifier: Justifier, case: DebuggerCase) -> list[str]:
    answer_sets = ComputeAnswerSetsService(justifier, case.answer_set_count).run()
    test.assertIsNotNone(answer_sets, f"{case.name}: expected a satisfiable program.")
    return answer_sets or []


def _normalize_answer_set(answer_set: str | list[str]) -> tuple[str, ...]:
    if isinstance(answer_set, str):
        atoms = asp_parser.split_top_level(answer_set)
    else:
        atoms = [str(atom) for atom in answer_set]
    return tuple(sorted(_clean_piece(atom) for atom in atoms if _clean_piece(atom)))


def _atom_to_record(atom: QueryAtom) -> dict[str, Any]:
    return {
        "atom": atom.atom,
        "text": str(atom),
        "value": atom.value,
    }


def _expected_atom_to_record(atom: str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(atom, str):
        return {"text": atom}

    record = dict(atom)
    if isinstance(record.get("value"), str):
        record["value"] = ATOM_VALUES[record["value"].lower()]
    return record


def _find_query_atom(justifier: Justifier, atoms: list[QueryAtom], literal: str) -> QueryAtom:
    literal = _clean_piece(literal)
    clean_atom = literal.removeprefix("not ").removesuffix(".")
    for atom in atoms:
        if str(atom) == literal or atom.atom == clean_atom:
            return atom
    return justifier.derive_query_atom(literal)


def _response_to_record(response: Response) -> dict[str, Any]:
    return {
        "type": response.type,
        "rule": response.rule,
    }


def _expected_response_to_record(response: str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(response, str):
        return {"rule": response}

    record = dict(response)
    if isinstance(record.get("type"), str):
        record["type"] = RESPONSE_TYPES[record["type"].lower()]
    return record


def _normalize_aggregate_mapping(mapping: dict[str, dict[str, list[str]]]) -> dict[str, dict[str, list[str]]]:
    normalized: dict[str, dict[str, list[str]]] = {}
    for key, groups in mapping.items():
        normalized_groups: dict[str, list[str]] = {}
        for group_label, atoms in groups.items():
            normalized_groups[str(group_label)] = [_clean_piece(atom) for atom in atoms if _clean_piece(atom)]
        normalized[str(key)] = normalized_groups
    return normalized


def _normalize_truth_mapping(mapping: dict[str, str]) -> dict[str, str]:
    return {str(key): _clean_piece(value) for key, value in mapping.items()}


def _assert_sequence(test: TestCase, actual: list[Any], expected: list[Any], *, ordered: bool) -> None:
    if ordered:
        test.assertEqual(actual, expected)
        return

    test.assertEqual(
        sorted(json.dumps(item, sort_keys=True) for item in actual),
        sorted(json.dumps(item, sort_keys=True) for item in expected),
    )


def _record_matches(actual: dict[str, Any], expected: dict[str, Any]) -> bool:
    return all(actual.get(key) == value for key, value in expected.items())


def _clean_piece(value: Any) -> str:
    return str(value).strip().lstrip(",").strip()
