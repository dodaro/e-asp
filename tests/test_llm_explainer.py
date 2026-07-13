from __future__ import annotations

from unittest import TestCase

from easp.llm_explainer import (
    AggregateDetail,
    AggregateElement,
    _aggregate_details_section,
    _responses_section,
)
from easp.models import FREE_CHOICE_EXPLANATION


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


class FreeChoicePromptTests(TestCase):
    def test_empty_literal_explanation_is_described_to_the_llm(self) -> None:
        self.assertEqual(
            _responses_section([], free_choice=True),
            "Generated explanation: " + FREE_CHOICE_EXPLANATION,
        )

    def test_other_empty_explanation_types_are_not_misclassified(self) -> None:
        self.assertEqual(_responses_section([], free_choice=False), "")
