from __future__ import annotations

from unittest import TestCase

from easp import asp_parser


class AggregateExpressionTests(TestCase):
    def test_extracts_each_aggregate_with_its_assignment(self) -> None:
        rule = (
            "maggiori_c :- #count{X: b(X)} = V1, "
            "#count{X: c(X)} = V2, V1 < V2."
        )

        self.assertEqual(
            asp_parser.aggregate_expressions(rule),
            [
                "#count{X: b(X)} = V1",
                "#count{X: c(X)} = V2",
            ],
        )

    def test_removes_aggregate_guards_but_keeps_their_comparison(self) -> None:
        body = "#count{X: b(X)} = V1, #count{X: c(X)} = V2, V1 < V2"

        remaining = asp_parser.without_aggregate_expressions(body)

        self.assertEqual(
            [block for block in asp_parser.split_top_level(remaining) if block],
            ["V1 < V2"],
        )

    def test_default_negation_is_part_of_the_aggregate_expression(self) -> None:
        body = "not 2 <= #count{X: p(X)}, enabled"

        self.assertEqual(
            asp_parser.aggregate_expressions(body),
            ["not 2 <= #count{X: p(X)}"],
        )
        self.assertTrue(
            asp_parser.aggregate_is_default_negated(
                asp_parser.aggregate_expression(body)
            )
        )

    def test_removing_a_negated_aggregate_does_not_leave_not_behind(self) -> None:
        body = "not #sum{W: p(W)} >= 3, enabled"

        remaining = asp_parser.without_aggregate_expressions(body)

        self.assertEqual(
            [block for block in asp_parser.split_top_level(remaining) if block],
            ["enabled"],
        )
