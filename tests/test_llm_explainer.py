from __future__ import annotations

from unittest import TestCase
from unittest.mock import Mock

from easp.llm_explainer import (
    AggregateDetail,
    AggregateElement,
    _aggregate_details_section,
    _responses_section,
    _weak_constraints_section,
)
from easp.models import (
    FREE_CHOICE_EXPLANATION,
    CostLevel,
    WeakConstraint,
    WeakConstraintInstance,
)
from easp.ui.actions import _aggregate_elements_for_prompt


class AggregatePromptTests(TestCase):
    def test_prompt_uses_element_notation_without_group_wording(self) -> None:
        text = _aggregate_details_section(
            [
                AggregateDetail(
                    rule="q :- #count{X:p(X)} >= 1.",
                    key="#count{X:p(X)} >= 1",
                    truth_message="contributes to the result",
                    elements=[
                        AggregateElement(label="<X=1>", atoms=["p(1) is true"])
                    ],
                )
            ]
        )

        self.assertIn("Relevant aggregate elements:", text)
        self.assertNotIn("group", text.casefold())

    def test_exact_aggregate_omits_secondary_false_elements(self) -> None:
        justifier = Mock()
        justifier.aggregate_uses_exact_comparison.return_value = True

        elements = _aggregate_elements_for_prompt(
            justifier,
            "#count{X:p(X)} = 1",
            {
                "<X=1>": ["p(1) is true"],
                "<X=2>": ["p(2) is false"],
            },
        )

        self.assertEqual(
            elements,
            [AggregateElement(label="<X=1>", atoms=["p(1) is true"])],
        )
        prompt_section = _aggregate_details_section(
            [
                AggregateDetail(
                    rule="q :- #count{X:p(X)} = 1.",
                    key="#count{X:p(X)} = 1",
                    truth_message="the aggregate condition is true",
                    elements=elements,
                )
            ]
        )
        self.assertIn("p(1) is true", prompt_section)
        self.assertNotIn("p(2) is false", prompt_section)

    def test_non_exact_aggregate_keeps_its_false_causal_elements(self) -> None:
        justifier = Mock()
        justifier.aggregate_uses_exact_comparison.return_value = False

        elements = _aggregate_elements_for_prompt(
            justifier,
            "#count{X:p(X)} >= 2",
            {"<X=2>": ["p(2) is false"]},
        )

        self.assertEqual(
            elements,
            [AggregateElement(label="<X=2>", atoms=["p(2) is false"])],
        )


class FreeChoicePromptTests(TestCase):
    def test_empty_literal_explanation_is_described_to_the_llm(self) -> None:
        self.assertEqual(
            _responses_section([], free_choice=True),
            "Generated explanation: " + FREE_CHOICE_EXPLANATION,
        )

    def test_other_empty_explanation_types_are_not_misclassified(self) -> None:
        self.assertEqual(_responses_section([], free_choice=False), "")


class WeakConstraintPromptTests(TestCase):
    def setUp(self) -> None:
        self.constraints = [
            WeakConstraint(
                rule=":~ shift(N,1). [3@1,N]",
                level="1",
                cost=3,
                instances=[WeakConstraintInstance("a", 3)],
            ),
            WeakConstraint(rule=":~ shift(N,2). [5@1,N]", level="1"),
        ]

    def test_violated_and_unviolated_constraints_are_both_reported(self) -> None:
        text = _weak_constraints_section(CostLevel("1", 3), self.constraints)

        self.assertIn("total cost 3, 1 of 2 violated", text)
        self.assertIn(":~ shift(N,1). [3@1,N] -> violated, cost 3", text)
        self.assertIn(":~ shift(N,2). [5@1,N] -> not violated, cost 0", text)
        self.assertIn("instance a: cost 3", text)

    def test_placeholder_terms_are_spelled_out(self) -> None:
        text = _weak_constraints_section(
            CostLevel("1", 4),
            [
                WeakConstraint(
                    rule=":~ a. [4@1]",
                    level="1",
                    cost=4,
                    instances=[WeakConstraintInstance("empty", 4)],
                )
            ],
        )

        self.assertIn("instance no terms: cost 4", text)

    def test_section_is_omitted_outside_the_optimality_explanation(self) -> None:
        self.assertEqual(_weak_constraints_section(None, self.constraints), "")
        self.assertEqual(_weak_constraints_section(CostLevel("1", 0), []), "")
