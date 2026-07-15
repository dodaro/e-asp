from __future__ import annotations

from collections import OrderedDict
from unittest import TestCase

from easp.debugger import Debugger
from easp.models import QueryAtom


class AggregateExplanationCasesTests(TestCase):
    def test_case_1_true_aggregate_with_analyzed_literal_in_set(self) -> None:
        debugger = self._debugger(
            true_atoms=["p(1)", "p(2)"],
            false_atoms=["p(3)"],
            order=["p(1)", "p(2)", "not p(3)"],
            analyzed=QueryAtom("p(1)", QueryAtom.TRUE),
        )

        selected = debugger._select_aggregate_explanation(
            self._entry(3),
            "#count{X:p(X)} >= 2",
            count=True,
            internal=True,
        )

        self.assertEqual(selected, {"3": ["p(3) is false"]})

    def test_case_2_false_aggregate_with_analyzed_literal_in_set(self) -> None:
        debugger = self._debugger(
            true_atoms=["p(1)", "p(2)"],
            false_atoms=["p(3)"],
            order=["p(1)", "p(2)", "not p(3)"],
            analyzed=QueryAtom("p(1)", QueryAtom.TRUE),
        )

        selected = debugger._select_aggregate_explanation(
            self._entry(3),
            "#count{X:p(X)} >= 3",
            count=True,
            internal=True,
        )

        self.assertEqual(selected, {"2": ["p(2) is true"]})

    def test_internal_true_upper_bound_uses_other_true_elements(self) -> None:
        debugger = self._debugger(
            true_atoms=["p(1)", "p(2)"],
            false_atoms=["p(3)"],
            order=["p(1)", "p(2)", "not p(3)"],
            analyzed=QueryAtom("p(1)", QueryAtom.TRUE),
        )

        selected = debugger._select_aggregate_explanation(
            self._entry(3),
            "#count{X:p(X)} <= 2",
            count=True,
            internal=True,
        )

        self.assertEqual(selected, {"2": ["p(2) is true"]})

    def test_internal_false_upper_bound_uses_false_elements(self) -> None:
        debugger = self._debugger(
            true_atoms=["p(1)", "p(2)"],
            false_atoms=["p(3)"],
            order=["p(1)", "p(2)", "not p(3)"],
            analyzed=QueryAtom("p(1)", QueryAtom.TRUE),
        )

        selected = debugger._select_aggregate_explanation(
            self._entry(3),
            "#count{X:p(X)} <= 1",
            count=True,
            internal=True,
        )

        self.assertEqual(selected, {"3": ["p(3) is false"]})

    def test_case_3_true_aggregate_uses_only_sufficient_prefix(self) -> None:
        debugger = self._debugger(
            true_atoms=["p(1)", "p(2)", "p(3)"],
            false_atoms=["p(4)"],
            order=["p(1)", "p(2)", "p(3)", "not p(4)"],
        )

        selected = debugger._select_aggregate_explanation(
            self._entry(4),
            "#count{X:p(X)} >= 2",
            count=True,
            internal=False,
        )

        self.assertEqual(
            selected,
            {
                "1": ["p(1) is true"],
                "2": ["p(2) is true"],
            },
        )

    def test_case_4_false_aggregate_uses_its_true_complement(self) -> None:
        debugger = self._debugger(
            true_atoms=["p(1)"],
            false_atoms=["p(2)", "p(3)"],
            order=["p(1)", "not p(2)", "not p(3)"],
        )

        selected = debugger._select_aggregate_explanation(
            self._entry(3),
            "#count{X:p(X)} >= 2",
            count=True,
            internal=False,
        )

        self.assertEqual(
            selected,
            {
                "2": ["p(2) is false"],
                "3": ["p(3) is false"],
            },
        )

    def test_sum_prefix_uses_weights_instead_of_element_count(self) -> None:
        debugger = self._debugger(
            true_atoms=["p(1)", "p(2)", "p(3)"],
            false_atoms=[],
            order=["p(1)", "p(2)", "p(3)"],
        )
        entry = OrderedDict(
            [
                ("2,a", ["p(1)"]),
                ("2,b", ["p(2)"]),
                ("5,c", ["p(3)"]),
            ]
        )

        selected = debugger._select_aggregate_explanation(
            entry,
            "#sum{W,X:p(X)} >= 3",
            count=False,
            internal=False,
        )

        self.assertEqual(
            selected,
            {
                "2,a": ["p(1) is true"],
                "2,b": ["p(2) is true"],
            },
        )

    def test_true_upper_bound_above_total_weight_needs_no_elements(self) -> None:
        debugger = self._debugger(
            true_atoms=["p(1)"],
            false_atoms=["p(2)", "p(3)"],
            order=["p(1)", "not p(2)", "not p(3)"],
        )

        selected = debugger._select_aggregate_explanation(
            self._entry(3),
            "#count{X:p(X)} <= 4",
            count=True,
            internal=False,
        )

        self.assertEqual(selected, {})

    def test_not_equal_uses_both_true_and_false_elements(self) -> None:
        debugger = self._debugger(
            true_atoms=["p(1)"],
            false_atoms=["p(2)", "p(3)"],
            order=["p(1)", "not p(2)", "not p(3)"],
        )

        selected = debugger._select_aggregate_explanation(
            self._entry(3),
            "#count{X:p(X)} != 2",
            count=True,
            internal=False,
        )

        self.assertEqual(
            selected,
            {
                "1": ["p(1) is true"],
                "2": ["p(2) is false"],
                "3": ["p(3) is false"],
            },
        )

    def test_exact_comparison_detection_includes_equal_and_not_equal(self) -> None:
        self.assertTrue(Debugger.aggregate_uses_exact_comparison("#count{X:p(X)} = 1"))
        self.assertTrue(Debugger.aggregate_uses_exact_comparison("#count{X:p(X)} != 1"))
        self.assertFalse(Debugger.aggregate_uses_exact_comparison("#count{X:p(X)} >= 1"))

    def test_literal_inside_a_conjunction_is_not_treated_as_set_element(self) -> None:
        debugger = self._debugger(
            true_atoms=["p(1)", "q(1)"],
            false_atoms=[],
            order=["p(1)", "q(1)"],
            analyzed=QueryAtom("p(1)", QueryAtom.TRUE),
        )

        self.assertFalse(
            debugger._condition_contains_analyzed_literal("p(1),q(1)")
        )

    @staticmethod
    def _entry(size: int) -> OrderedDict[str, list[str]]:
        return OrderedDict(
            (str(index), [f"p({index})"])
            for index in range(1, size + 1)
        )

    @staticmethod
    def _debugger(
        *,
        true_atoms: list[str],
        false_atoms: list[str],
        order: list[str],
        analyzed: QueryAtom | None = None,
    ) -> Debugger:
        debugger = Debugger(True, True, "")
        debugger.derived_atoms = true_atoms
        debugger.false_atoms = false_atoms
        debugger.order = order
        debugger.analyzed = analyzed
        return debugger


class NegatedAggregateTests(TestCase):
    def test_true_not_count_is_explained_by_false_elements(self) -> None:
        debugger = AggregateExplanationCasesTests._debugger(
            true_atoms=["p(1)"],
            false_atoms=["p(2)", "p(3)"],
            order=["p(1)", "not p(2)", "not p(3)"],
        )
        entry = AggregateExplanationCasesTests._entry(3)
        expression = "not #count{X:p(X)} >= 2"

        self.assertTrue(debugger._grounded_aggregate_truth(expression, 1))
        self.assertEqual(
            debugger._select_aggregate_explanation(
                entry,
                expression,
                count=True,
                internal=False,
            ),
            {
                "2": ["p(2) is false"],
                "3": ["p(3) is false"],
            },
        )

    def test_false_not_count_is_explained_by_true_elements(self) -> None:
        debugger = AggregateExplanationCasesTests._debugger(
            true_atoms=["p(1)", "p(2)"],
            false_atoms=["p(3)"],
            order=["p(1)", "p(2)", "not p(3)"],
        )
        entry = AggregateExplanationCasesTests._entry(3)
        expression = "not 2 <= #count{X:p(X)}"

        self.assertFalse(debugger._grounded_aggregate_truth(expression, 2))
        self.assertEqual(
            debugger._select_aggregate_explanation(
                entry,
                expression,
                count=True,
                internal=False,
            ),
            {
                "1": ["p(1) is true"],
                "2": ["p(2) is true"],
            },
        )

    def test_not_sum_uses_the_same_complement_semantics(self) -> None:
        debugger = AggregateExplanationCasesTests._debugger(
            true_atoms=["p(1)"],
            false_atoms=["p(2)", "p(3)"],
            order=["p(1)", "not p(2)", "not p(3)"],
        )
        entry = OrderedDict(
            [
                ("1", ["p(1)"]),
                ("2", ["p(2)"]),
                ("3", ["p(3)"]),
            ]
        )
        expression = "not #sum{W:p(W)} >= 4"

        self.assertTrue(debugger._grounded_aggregate_truth(expression, 1))
        self.assertEqual(
            debugger._select_aggregate_explanation(
                entry,
                expression,
                count=False,
                internal=False,
            ),
            {
                "2": ["p(2) is false"],
                "3": ["p(3) is false"],
            },
        )

    def test_external_body_removal_consumes_not_with_the_aggregate(self) -> None:
        debugger = Debugger(True, True, "")

        self.assertEqual(
            debugger.get_external(
                "q :- enabled, not #count{X:p(X)} >= 2."
            ),
            "enabled",
        )


class RuleHeadParsingTests(TestCase):
    def test_body_aggregate_elements_are_not_rule_head_candidates(self) -> None:
        self.assertEqual(
            Debugger._rule_head_candidates(
                ":- not a4, #count{a1:a1;a2:a2;a3:a3} >= 2."
            ),
            [],
        )

    def test_disjunction_and_choice_heads_are_still_recognized(self) -> None:
        self.assertEqual(
            Debugger._rule_head_candidates("a1 | a2 :- enabled."),
            ["a1", "a2"],
        )
        self.assertEqual(
            Debugger._rule_head_candidates("{a1;a2} :- enabled."),
            ["a1", "a2"],
        )
