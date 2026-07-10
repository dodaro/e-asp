from __future__ import annotations

from unittest import TestCase

from tests.debugger_case_runner import (
    assert_aggregate_expansions,
    assert_answer_sets,
    assert_atoms,
    assert_literal_explanations,
    assert_unsat_debug,
    load_cases,
)


class DebuggerFixtureTests(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.cases = load_cases()

    def test_answer_sets(self) -> None:
        self._require_cases()
        for case in self.cases:
            with self.subTest(case=case.name):
                assert_answer_sets(self, case)

    def test_atoms_available_for_inspection(self) -> None:
        self._require_cases()
        for case in self.cases:
            with self.subTest(case=case.name):
                assert_atoms(self, case)

    def test_literal_explanations(self) -> None:
        self._require_cases()
        for case in self.cases:
            with self.subTest(case=case.name):
                assert_literal_explanations(self, case)

    def test_unsat_debugging(self) -> None:
        self._require_cases()
        for case in self.cases:
            with self.subTest(case=case.name):
                assert_unsat_debug(self, case)

    def test_aggregate_expansions(self) -> None:
        self._require_cases()
        for case in self.cases:
            with self.subTest(case=case.name):
                assert_aggregate_expansions(self, case)

    def _require_cases(self) -> None:
        if not self.cases:
            self.skipTest("No debugger cases found in tests/fixtures/debugger_cases.")

