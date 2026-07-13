from __future__ import annotations

from unittest import TestCase
from unittest.mock import Mock

from easp.llm_explainer import (
    AggregateDetail,
    AggregateElement,
    _aggregate_details_section,
    _responses_section,
)
from easp.models import FREE_CHOICE_EXPLANATION
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
