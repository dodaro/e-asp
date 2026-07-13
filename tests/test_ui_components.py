from __future__ import annotations

from unittest import TestCase
from unittest.mock import patch

from easp.models import Response
from easp.ui import components
from easp.ui.components import (
    _aggregate_group_label,
    _is_false_aggregate_evaluation,
)


class AggregateGroupLabelTests(TestCase):
    def test_binding_label_is_shown_without_group_prefix(self) -> None:
        self.assertEqual(
            _aggregate_group_label("<D=2, PH=1>"),
            "<D=2, PH=1>",
        )

    def test_opaque_label_keeps_group_prefix_as_fallback(self) -> None:
        self.assertEqual(_aggregate_group_label("2,1"), "Group 2,1")


class AggregateEvaluationTests(TestCase):
    def test_false_aggregate_message_is_secondary(self) -> None:
        self.assertTrue(
            _is_false_aggregate_evaluation(
                "the aggregate is false, expand to see why"
            )
        )

    def test_true_aggregate_message_remains_primary(self) -> None:
        self.assertFalse(
            _is_false_aggregate_evaluation(
                "the aggregate is true, expand to see why"
            )
        )

    def test_false_comparison_message_remains_primary(self) -> None:
        self.assertFalse(
            _is_false_aggregate_evaluation("the comparison is false")
        )


class RuleGroupTests(TestCase):
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
