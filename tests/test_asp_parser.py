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


class OptimizationElementTests(TestCase):
    def test_weak_constraint_yields_a_single_element(self) -> None:
        self.assertEqual(
            asp_parser.optimization_elements(":~ shift(N,1). [3@1,N]"),
            [asp_parser.OptimizationElement("3", "1", "N", "shift(N,1)")],
        )

    def test_each_element_of_a_minimize_keeps_its_own_weight_and_body(self) -> None:
        self.assertEqual(
            asp_parser.optimization_elements(
                "#minimize{ 2@1,X : q(X) ; 1@2,X : p(X), not q(X) }."
            ),
            [
                asp_parser.OptimizationElement("2", "1", "X", "q(X)"),
                asp_parser.OptimizationElement("1", "2", "X", "p(X), not q(X)"),
            ],
        )

    def test_a_missing_level_means_level_zero(self) -> None:
        self.assertEqual(
            asp_parser.optimization_elements(":~ a. [4]"),
            [asp_parser.OptimizationElement("4", "0", "", "a")],
        )
        self.assertEqual(
            asp_parser.optimization_elements("#minimize{ 2,X : q(X) }."),
            [asp_parser.OptimizationElement("2", "0", "X", "q(X)")],
        )

    def test_an_element_without_condition_has_an_empty_body(self) -> None:
        self.assertEqual(
            asp_parser.optimization_elements("#minimize{ 1@1,a }."),
            [asp_parser.OptimizationElement("1", "1", "a", "")],
        )

    def test_colons_and_semicolons_nested_in_the_condition_do_not_split_it(self) -> None:
        self.assertEqual(
            asp_parser.optimization_elements(
                "#minimize{ 1@1,X : p(X), #count{Y : q(X,Y)} > 2 }."
            ),
            [
                asp_parser.OptimizationElement(
                    "1", "1", "X", "p(X), #count{Y : q(X,Y)} > 2"
                )
            ],
        )

    def test_commas_inside_string_terms_stay_in_the_discriminant(self) -> None:
        self.assertEqual(
            asp_parser.optimization_elements(':~ p("a,b"). [1@1,"a,b"]'),
            [asp_parser.OptimizationElement("1", "1", '"a,b"', 'p("a,b")')],
        )

    def test_plain_rules_carry_no_optimization_element(self) -> None:
        self.assertEqual(asp_parser.optimization_elements("q(X) :- p(X)."), [])


class OptimizationCostTests(TestCase):
    """A #maximize element pays the opposite of the weight written down, the
    way clingo rewrites it into a #minimize."""

    def _element(self, statement: str) -> asp_parser.OptimizationElement:
        return asp_parser.optimization_elements(statement)[0]

    def test_a_minimized_weight_is_its_own_cost(self) -> None:
        element = self._element("#minimize{ 5@2,X : r(X) }.")
        self.assertFalse(element.maximize)
        self.assertEqual((element.weight, element.cost), ("5", "5"))

    def test_a_maximized_weight_costs_its_opposite(self) -> None:
        element = self._element("#maximize{ 5@2,X : r(X) }.")
        self.assertTrue(element.maximize)
        self.assertEqual((element.weight, element.cost), ("5", "-5"))

    def test_a_negative_maximized_weight_costs_a_positive_one(self) -> None:
        self.assertEqual(self._element("#maximize{ -5@2 : r }.").cost, "5")

    def test_a_maximized_expression_stays_a_valid_term(self) -> None:
        self.assertEqual(self._element("#maximize{ W@1,X : v(X,W) }.").cost, "-(W)")

    def test_a_weak_constraint_is_never_maximized(self) -> None:
        element = self._element(":~ a. [3@1]")
        self.assertFalse(element.maximize)
        self.assertEqual(element.cost, "3")

