from __future__ import annotations

from unittest import TestCase

from easp.ui.components import _aggregate_group_label


class AggregateGroupLabelTests(TestCase):
    def test_binding_label_is_shown_without_group_prefix(self) -> None:
        self.assertEqual(
            _aggregate_group_label("<D=2, PH=1>"),
            "<D=2, PH=1>",
        )

    def test_opaque_label_keeps_group_prefix_as_fallback(self) -> None:
        self.assertEqual(_aggregate_group_label("2,1"), "Group 2,1")
