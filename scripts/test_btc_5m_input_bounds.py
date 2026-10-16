#!/usr/bin/env python3
"""Input-bound contracts that main's W3-W6 suites do not lock.

No network. No live posting.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from btc_5m_maker_pilot import (
    MakerPilotConfig,
    ShadowQuote,
    SideBook,
    evaluate_cancel_rules,
    favored_side_from_signal,
)
from btc_5m_twap_fair import (
    p_up_from_projected,
    projected_final_twap_fair,
    residual_twap_vol,
)
from btc_5m_winmore_gates import should_block_second_clip


def _book(side: str, bid: float, ask: float, token_id: str = "tok") -> SideBook:
    return SideBook(side=side, token_id=token_id, best_bid=bid, best_ask=ask)


def _quote(**kwargs) -> ShadowQuote:
    base = dict(
        quote_id="sh-1",
        side="UP",
        token_id="tok",
        price=0.44,
        size_shares=10.0,
        order_type="GTD",
        post_only=True,
        posted_at=1_000.0,
        gtd_expire_at=1_015.0,
        quote_mid=0.445,
        quote_spread=0.01,
        fair_signal_at_post="up_favored",
        status="live",
    )
    base.update(kwargs)
    return ShadowQuote(**base)


class ResidualVolInputTests(unittest.TestCase):
    def test_should_zero_residual_vol_when_daily_vol_is_negative(self):
        self.assertEqual(residual_twap_vol(20.0, -5.0), 0.0)
        self.assertEqual(residual_twap_vol(240.0, -0.01), 0.0)

    def test_should_treat_negative_vol_as_a_locked_fair_print(self):
        up = projected_final_twap_fair(100_000.0, 100_200.0, 20.0, btc_daily_vol_pct=-5.0)
        down = projected_final_twap_fair(100_000.0, 99_800.0, 20.0, btc_daily_vol_pct=-5.0)
        self.assertEqual(up["residual_vol"], 0.0)
        self.assertEqual(up["p_up"], 1.0)
        self.assertEqual(down["p_up"], 0.0)


class OpenTwapNonPositiveTests(unittest.TestCase):
    def test_should_coin_flip_when_window_open_twap_is_non_positive(self):
        self.assertEqual(p_up_from_projected(0.0, 100_500.0, 0.02), 0.5)
        self.assertEqual(p_up_from_projected(-1.0, 100_500.0, 0.02), 0.5)


class FavoredSideAliasTests(unittest.TestCase):
    def test_should_strip_and_casefold_fair_signal_aliases(self):
        self.assertEqual(favored_side_from_signal(" UP_FAVORED "), "UP")
        self.assertEqual(favored_side_from_signal("Up"), "UP")
        self.assertEqual(favored_side_from_signal(" down "), "DOWN")
        self.assertIsNone(favored_side_from_signal("  "))
        self.assertIsNone(favored_side_from_signal(None))  # type: ignore[arg-type]

    def test_should_not_fair_flip_when_padded_signal_still_matches_quote_side(self):
        quote = _quote(side="UP")
        book = _book("UP", 0.44, 0.45)
        cfg = MakerPilotConfig(enabled=True, cancel_spread_widen_abs=0.03)
        d = evaluate_cancel_rules(
            quote, book, " up_favored ", now=1_005.0, config=cfg
        )
        self.assertFalse(d.cancel)
        self.assertIsNone(d.reason)


class SecondClipZeroFirstEdgeTests(unittest.TestCase):
    def test_should_not_decay_block_when_first_edge_is_zero_and_current_clears_min(self):
        self.assertFalse(should_block_second_clip(0.0, 0.02, 0.01, decay_ratio=0.50))

    def test_should_still_block_when_current_is_below_required_min(self):
        self.assertTrue(should_block_second_clip(0.0, 0.005, 0.01, decay_ratio=0.50))


if __name__ == "__main__":
    unittest.main(verbosity=2)
