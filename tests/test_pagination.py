"""`services/pagination.py` -- the page-number strip.

Ported from `restaurant-manager` along with the module, because the whole
point of sharing the arithmetic is that the two apps agree about when an
ellipsis appears.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.services.pagination import pagination_window  # noqa: E402


class TestPaginationWindow(unittest.TestCase):
    def test_one_page_is_just_page_one(self):
        self.assertEqual(pagination_window(1, 1), [1])
        self.assertEqual(pagination_window(1, 0), [1])

    def test_short_runs_list_every_page(self):
        self.assertEqual(pagination_window(1, 3), [1, 2, 3])

    def test_collapses_the_far_side(self):
        self.assertEqual(pagination_window(1, 9), [1, 2, None, 9])

    def test_collapses_both_sides_in_the_middle(self):
        self.assertEqual(pagination_window(5, 9), [1, None, 4, 5, 6, None, 9])

    def test_no_ellipsis_for_a_gap_of_exactly_one(self):
        # 1, then 3-4-5: the gap is page 2 alone, and an "…" standing in for
        # a single page is wider than the page it hides.
        self.assertEqual(pagination_window(4, 5), [1, None, 3, 4, 5])
        self.assertEqual(pagination_window(3, 5), [1, 2, 3, 4, 5])

    def test_the_real_queue(self):
        # 408 pending products, deep in the run.
        self.assertEqual(pagination_window(300, 408), [1, None, 299, 300, 301, None, 408])
