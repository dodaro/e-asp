from __future__ import annotations

from unittest import TestCase
from unittest.mock import patch

from easp.models import Response
from easp.models import FREE_CHOICE_EXPLANATION
from easp.ui import components
from easp.ui.components import (
    _aggregate_element_label,
    _partition_aggregate_values,
)


class AggregateElementLabelTests(TestCase):
    def test_binding_label_is_shown_without_group_prefix(self) -> None:
        self.assertEqual(
            _aggregate_element_label("<D=2, PH=1>"),
            "<D=2, PH=1>",
        )

    def test_opaque_label_is_shown_as_a_tuple(self) -> None:
        self.assertEqual(_aggregate_element_label("2,1"), "<2,1>")


class AggregateElementPartitionTests(TestCase):
    def test_false_elements_are_separated_from_true_ones(self) -> None:
        true_values, false_values = _partition_aggregate_values(
            {
                "<X=1>": ["p(1) is true"],
                "<X=2>": ["p(2) is false"],
                "<X=3>": ["p(3) is false", "q(3) is false"],
            }
        )

        self.assertEqual(true_values, {"<X=1>": ["p(1) is true"]})
        self.assertEqual(
            false_values,
            {
                "<X=2>": ["p(2) is false"],
                "<X=3>": ["p(3) is false", "q(3) is false"],
            },
        )

    def test_empty_annotations_stay_in_the_primary_section(self) -> None:
        true_values, false_values = _partition_aggregate_values({"<X=1>": []})

        self.assertEqual(true_values, {"<X=1>": []})
        self.assertEqual(false_values, {})


class RuleRenderingTests(TestCase):
    def test_plain_rules_precede_each_aggregate_rule_with_its_details(self) -> None:
        rendered: list[tuple[str, str]] = []
        responses = [
            Response("aggregate rule", components.AGGREGATE_TYPE),
            Response("plain rule", components.RULE_TYPE),
        ]

        with (
            patch.object(
                components.st,
                "code",
                side_effect=lambda rule, **_: rendered.append(("plain", rule)),
            ),
            patch.object(
                components,
                "render_aggregate",
                side_effect=lambda rule: rendered.append(("aggregate", rule)),
            ),
        ):
            components._render_rule_group(responses)

        self.assertEqual(
            rendered,
            [("plain", "plain rule"), ("aggregate", "aggregate rule")],
        )

    def test_empty_literal_explanation_is_reported_as_solver_choice(self) -> None:
        with (
            patch.object(components.st, "info") as info,
            patch.object(components, "render_llm_explanation_panel"),
        ):
            components.render_response_groups([], allow_literal_explain=True)

        info.assert_called_once_with(FREE_CHOICE_EXPLANATION)
