from __future__ import annotations

from unittest import TestCase
from unittest.mock import patch

from easp.models import FREE_CHOICE_EXPLANATION, QueryAtom, Response
from easp.services import partition_aggregate_values
from easp.ui import components
from easp.ui.components import (
    _aggregate_element_label,
    _atoms_of,
    _filter_inspection_atoms,
    _literal_predicate,
    _active_terms_caption,
    _matches_query,
    _query_terms,
    _weak_instance_label,
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
        true_values, false_values = partition_aggregate_values(
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
        true_values, false_values = partition_aggregate_values({"<X=1>": []})

        self.assertEqual(true_values, {"<X=1>": []})
        self.assertEqual(false_values, {})


class InspectionLiteralTests(TestCase):
    def setUp(self) -> None:
        self.atoms = [
            QueryAtom('duration("pat1",1,2)', QueryAtom.TRUE),
            QueryAtom('duration("pat2",1,3)', QueryAtom.FALSE),
            QueryAtom('reg("pat1","bed")', QueryAtom.TRUE),
            QueryAtom("ready", QueryAtom.FALSE),
            QueryAtom("inactive(20)", QueryAtom.FALSE),
            QueryAtom("active(20)", QueryAtom.TRUE),
        ]

    def test_predicate_is_extracted_from_atoms_with_and_without_arguments(self) -> None:
        self.assertEqual(_literal_predicate(self.atoms[0]), "duration")
        self.assertEqual(_literal_predicate(self.atoms[3]), "ready")

    def test_search_matches_the_visible_negative_literal(self) -> None:
        filtered = _filter_inspection_atoms(
            self.atoms,
            query="not duration",
            truth_filter="All",
            predicate_filter="All predicates",
        )

        self.assertEqual(filtered, [self.atoms[1]])

    def test_truth_and_predicate_filters_can_be_combined(self) -> None:
        filtered = _filter_inspection_atoms(
            self.atoms,
            query="",
            truth_filter="True",
            predicate_filter="duration",
        )

        self.assertEqual(filtered, [self.atoms[0]])

    def test_predicate_search_does_not_match_inside_another_predicate(self) -> None:
        filtered = _filter_inspection_atoms(
            self.atoms,
            query="active(20)",
            truth_filter="All",
            predicate_filter="All predicates",
        )

        self.assertEqual(filtered, [self.atoms[5]])


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


class WeakConstraintRenderingTests(TestCase):
    def test_discriminant_terms_are_shown_as_written(self) -> None:
        self.assertEqual(_weak_instance_label('"pat1",1'), '"pat1",1')

    def test_missing_terms_are_named_instead_of_showing_the_placeholder(self) -> None:
        self.assertEqual(_weak_instance_label("empty"), "(no terms)")
        self.assertEqual(_weak_instance_label("  "), "(no terms)")


class AnswerSetListingTests(TestCase):
    def test_commas_inside_string_constants_do_not_split_an_atom(self) -> None:
        self.assertEqual(
            _atoms_of('day(1), reg("pat,1","bed"), busy(2)'),
            ["day(1)", 'reg("pat,1","bed")', "busy(2)"],
        )

    def test_an_empty_answer_set_has_no_atom(self) -> None:
        self.assertEqual(_atoms_of(""), [])

    def test_an_answer_set_is_searched_like_a_literal(self) -> None:
        answer_set = 'day(1), shift("carl",1), busy(1)'

        self.assertTrue(_matches_query(answer_set, 'shift("carl",1)'))
        self.assertTrue(_matches_query(answer_set, "busy"))
        self.assertTrue(_matches_query(answer_set, ""))
        self.assertFalse(_matches_query(answer_set, "free"))

    def test_the_search_matches_word_starts_and_not_substrings(self) -> None:
        self.assertFalse(_matches_query("inactive(1)", "active"))
        self.assertTrue(_matches_query("active(1)", "active"))


class SearchQueryTests(TestCase):
    def setUp(self) -> None:
        self.answer_set = 'day(1), shift("carl",1), shift("anna",3), busy(1)'

    def test_every_term_of_the_query_must_be_present(self) -> None:
        self.assertTrue(_matches_query(self.answer_set, "shift busy"))
        self.assertTrue(_matches_query(self.answer_set, "carl anna"))
        self.assertFalse(_matches_query(self.answer_set, "carl bob"))
        self.assertFalse(_matches_query(self.answer_set, "shift free"))

    def test_extra_whitespace_between_terms_is_ignored(self) -> None:
        self.assertEqual(_query_terms("  shift   busy  "), ["shift", "busy"])
        self.assertEqual(_query_terms("   "), [])

    def test_spaces_inside_a_term_do_not_start_a_new_one(self) -> None:
        self.assertEqual(
            _query_terms('reg("a b") p(1, 2) x'),
            ['reg("a b")', "p(1, 2)", "x"],
        )

    def test_an_empty_query_keeps_everything(self) -> None:
        self.assertTrue(_matches_query(self.answer_set, ""))
        self.assertTrue(_matches_query("", ""))

    def test_a_single_term_query_is_unchanged(self) -> None:
        self.assertTrue(_matches_query(self.answer_set, 'shift("carl",1)'))
        self.assertFalse(_matches_query(self.answer_set, "free"))

    def test_an_atom_can_be_typed_the_way_it_reads(self) -> None:
        """Users type shift(carl, 1); clingo prints shift("carl",1)."""
        for typed in (
            'shift("carl",1)',
            "shift(carl,1)",
            'shift("carl", 1)',
            "shift( carl , 1 )",
        ):
            with self.subTest(typed=typed):
                self.assertTrue(_matches_query(self.answer_set, typed))

    def test_a_different_atom_still_does_not_match(self) -> None:
        self.assertFalse(_matches_query(self.answer_set, "shift(bob,1)"))
        self.assertFalse(_matches_query(self.answer_set, "shift(carl,2)"))

    def test_negation_is_not_collapsed_into_the_predicate(self) -> None:
        self.assertTrue(_matches_query('not shift("a",1)', "shift"))
        self.assertTrue(_matches_query('not shift("a",1)', "not"))
        self.assertFalse(_matches_query('shift("a",1)', "not"))

    def test_the_caption_spells_out_the_applied_terms(self) -> None:
        self.assertEqual(
            _active_terms_caption("busy(1) free(2)"),
            " containing `busy(1)` and `free(2)`",
        )
        self.assertEqual(_active_terms_caption("   "), "")

