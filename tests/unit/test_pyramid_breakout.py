"""
피라미딩 브레이크아웃 전략 단위 테스트

검증 항목:
- 직전 저점 대비 10% 상승 시 진입
- 진입가 기준 10% 하락 시 손절
- 최고가 기준 10% 하락 시 익절 (트레일링 스탑)
- 직전 매수가 기준 10% 상승마다 피라미딩
- 청산 후 저점 재설정, 재진입 동작
- 파라미터 유효성 검사
"""

import numpy as np
import pandas as pd
import pytest

from src.strategy.base import TradingSignal
from src.strategy.pyramid_breakout import PyramidBreakoutStrategy


# ── 헬퍼 ─────────────────────────────────────────────────────────────────────

def make_df(prices: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {"open": prices, "high": prices, "low": prices, "close": prices, "volume": 1.0},
        index=pd.date_range("2024-01-01", periods=len(prices), freq="1D"),
    )


def default_strategy(**kwargs) -> PyramidBreakoutStrategy:
    return PyramidBreakoutStrategy(
        entry_pct=10.0, stop_pct=10.0, trail_pct=10.0, add_pct=10.0,
        unit_amount=100_000, **kwargs
    )


# ── 초기화 및 파라미터 검증 ───────────────────────────────────────────────────

class TestInit:
    def test_defaults(self):
        s = PyramidBreakoutStrategy()
        assert s.entry_pct == 10.0
        assert s.stop_pct == 10.0
        assert s.trail_pct == 10.0
        assert s.add_pct == 10.0
        assert s.unit_amount == 100_000.0

    def test_custom_params(self):
        s = PyramidBreakoutStrategy(entry_pct=5.0, stop_pct=3.0, unit_amount=500_000)
        assert s.entry_pct == 5.0
        assert s.stop_pct == 3.0
        assert s.unit_amount == 500_000

    def test_invalid_pct_zero(self):
        with pytest.raises(ValueError, match="entry_pct"):
            PyramidBreakoutStrategy(entry_pct=0)

    def test_invalid_pct_over_100(self):
        with pytest.raises(ValueError, match="stop_pct"):
            PyramidBreakoutStrategy(stop_pct=100)

    def test_invalid_unit_amount(self):
        with pytest.raises(ValueError, match="unit_amount"):
            PyramidBreakoutStrategy(unit_amount=-1000)

    def test_validate_params_valid(self):
        s = default_strategy()
        assert s.validate_params({"entry_pct": 5.0, "stop_pct": 5.0, "unit_amount": 200_000})

    def test_validate_params_invalid_pct(self):
        s = default_strategy()
        assert not s.validate_params({"entry_pct": 0})

    def test_validate_params_invalid_unit(self):
        s = default_strategy()
        assert not s.validate_params({"unit_amount": 0})

    def test_repr_contains_name(self):
        assert "PyramidBreakoutStrategy" in repr(PyramidBreakoutStrategy())


# ── 진입 시그널 ───────────────────────────────────────────────────────────────

class TestEntry:
    def test_flat_market_no_signals(self):
        """횡보장: 10% 상승 없으면 시그널 없음"""
        s = default_strategy()
        prices = [100.0] * 30
        signals = s.generate_signals(make_df(prices))
        assert (signals != TradingSignal.HOLD).sum() == 0

    def test_entry_on_10pct_rise_from_low(self):
        """저점 90 → 99.1 (> 90 × 1.1 = 99.000…01) 도달 시 매수"""
        # 부동소수점 오차 회피: 임계값보다 확실히 높은 99.1 사용
        prices = [100.0, 90.0, 99.1, 99.1]
        s = default_strategy()
        signals = s.generate_signals(make_df(prices))
        assert signals.iloc[2] == TradingSignal.BUY

    def test_no_entry_below_10pct(self):
        """저점 대비 9% 상승은 진입 안 함"""
        prices = [100.0, 90.0, 98.0, 98.0]  # 90 × 1.1 = 99.0… → 98 < 임계값
        s = default_strategy()
        signals = s.generate_signals(make_df(prices))
        assert (signals == TradingSignal.BUY).sum() == 0

    def test_low_updates_before_entry(self):
        """저점은 진입 전까지 계속 갱신된다"""
        # 100 → 95 → 90(새 저점) → 99.1 (> 90 × 1.1) → 진입
        prices = [100.0, 95.0, 90.0, 99.1]
        s = default_strategy()
        signals = s.generate_signals(make_df(prices))
        # 저점이 95가 아니라 90으로 갱신됐으므로 진입가는 99.1
        assert signals.iloc[3] == TradingSignal.BUY

    def test_no_entry_if_not_enough_rise_from_new_low(self):
        """새 저점(90)의 10% = 99, 95는 진입 불가"""
        prices = [100.0, 95.0, 90.0, 95.0]  # 95 < 99.0…
        s = default_strategy()
        signals = s.generate_signals(make_df(prices))
        assert (signals == TradingSignal.BUY).sum() == 0


# ── 손절 ─────────────────────────────────────────────────────────────────────

class TestStopLoss:
    def test_stop_loss_at_10pct_below_entry(self):
        """진입가(99.1) × 0.9 = 89.19 이하로 하락 시 손절"""
        # 저점 90 → 진입 99.1 → 하락 89.0 (< 89.19)
        prices = [100.0, 90.0, 99.1, 95.0, 89.0]
        s = default_strategy()
        signals = s.generate_signals(make_df(prices))
        assert signals.iloc[4] == TradingSignal.SELL

    def test_no_stop_above_threshold(self):
        """진입가의 90% 초과면 손절 안 함"""
        # 진입 99.1 → stop = 99.1 × 0.9 = 89.19 → 90 > 89.19
        prices = [100.0, 90.0, 99.1, 90.0]
        s = default_strategy()
        signals = s.generate_signals(make_df(prices))
        assert signals.iloc[3] != TradingSignal.SELL

    def test_stop_loss_resets_candidate_low(self):
        """손절 후 새 저점부터 재진입 로직이 시작됨"""
        # 저점 90 → 진입 99.1 → 손절 89.0
        # 청산 후 새 저점 80 → 88.1 (> 80 × 1.1 = 88.0…) → 재진입
        prices = [100.0, 90.0, 99.1, 89.0,   # 진입 후 손절
                  80.0, 88.1]                  # 80이 새 저점, 88.1 > 임계값 → 재진입
        s = default_strategy()
        signals = s.generate_signals(make_df(prices))
        assert signals.iloc[2] == TradingSignal.BUY   # 첫 진입
        assert signals.iloc[3] == TradingSignal.SELL  # 손절
        assert signals.iloc[5] == TradingSignal.BUY   # 재진입


# ── 익절 (트레일링 스탑) ──────────────────────────────────────────────────────

class TestTrailingStop:
    def test_trailing_stop_at_10pct_below_peak(self):
        """최고가(110) × 0.9 = 99 이하로 하락 시 익절"""
        # 진입 99 → 110(최고가) → 99(= 110 × 0.9) → 익절
        prices = [100.0, 90.0, 99.0, 110.0, 99.0]
        s = default_strategy()
        signals = s.generate_signals(make_df(prices))
        assert signals.iloc[4] == TradingSignal.SELL

    def test_trailing_stop_follows_peak(self):
        """최고가가 올라갈수록 익절 기준도 올라간다"""
        # 진입 99 → 110 → 120(새 최고가) → 108(= 120 × 0.9) → 익절
        prices = [100.0, 90.0, 99.0, 110.0, 120.0, 108.0]
        s = default_strategy()
        signals = s.generate_signals(make_df(prices))
        assert signals.iloc[5] == TradingSignal.SELL

    def test_no_trailing_stop_before_peak(self):
        """최고가보다 10% 안 떨어지면 익절 없음"""
        # 진입 99 → 120 → 110 (= 120 × 0.917 > 0.9) → 익절 아님
        prices = [100.0, 90.0, 99.0, 120.0, 110.0]
        s = default_strategy()
        signals = s.generate_signals(make_df(prices))
        assert signals.iloc[4] != TradingSignal.SELL


# ── 피라미딩 ─────────────────────────────────────────────────────────────────

class TestPyramiding:
    def test_pyramid_on_10pct_rise_from_entry(self):
        """진입가(99.1) × 1.1 = 109.01 이상 도달 시 추가 매수"""
        # 진입 99.1 → 피라미딩 기준 99.1 × 1.1 = 109.01 → 109.1 도달
        prices = [100.0, 90.0, 99.1, 105.0, 109.1]
        s = default_strategy()
        signals = s.generate_signals(make_df(prices))
        assert signals.iloc[2] == TradingSignal.BUY   # 첫 진입
        assert signals.iloc[4] == TradingSignal.BUY   # 피라미딩

    def test_multiple_pyramid_adds(self):
        """10%씩 연속 상승 → 매 스텝마다 추가 매수"""
        # 진입 100 → 110(+10%) → 121(+10%) → 133.1(+10%)
        prices = [90.0, 80.0, 88.0,   # 저점 80, 진입 88
                  96.8, 96.9,           # 88 × 1.1 = 96.8 → 피라미딩
                  106.5, 106.6]         # 96.9 × 1.1 ≈ 106.59 → 피라미딩
        s = default_strategy()
        signals = s.generate_signals(make_df(prices))
        buy_count = (signals == TradingSignal.BUY).sum()
        assert buy_count >= 3  # 첫 진입 + 피라미딩 2회 이상

    def test_no_pyramid_without_position(self):
        """포지션 없을 때는 피라미딩 없음 (첫 진입만)"""
        prices = [100.0, 90.0, 99.1]
        s = default_strategy()
        signals = s.generate_signals(make_df(prices))
        # BUY는 첫 진입 1개뿐
        assert (signals == TradingSignal.BUY).sum() == 1

    def test_pyramid_resets_add_price(self):
        """피라미딩 후 다음 추가 매수 기준은 직전 피라미딩 가격"""
        # 진입 100 → 110(피라미딩) → 115(아직 아님) → 121(110 × 1.1 = 121)
        prices = [90.0, 80.0, 88.0, 110.0, 115.0, 121.0]
        # 80 × 1.1 = 88 → 진입, 88 × 1.1 = 96.8 < 110 → 첫 피라미딩 at 110
        # 110 × 1.1 = 121 → 두 번째 피라미딩
        s = default_strategy()
        signals = s.generate_signals(make_df(prices))
        buy_signals = signals[signals == TradingSignal.BUY]
        assert len(buy_signals) >= 2


# ── 재진입 ────────────────────────────────────────────────────────────────────

class TestReentry:
    def test_reentry_after_trailing_stop(self):
        """익절 후 새 저점 대비 10% 상승 시 재진입"""
        prices = [
            100.0, 90.0, 99.1,    # 저점 90, 진입 99.1
            120.0, 107.0,          # 최고 120, 120 × 0.9 = 108 → 107 익절
            100.0, 110.1,          # 100이 새 저점, 110.1 > 100 × 1.1 → 재진입
        ]
        s = default_strategy()
        signals = s.generate_signals(make_df(prices))
        sell_signals = signals[signals == TradingSignal.SELL]
        buy_signals = signals[signals == TradingSignal.BUY]
        assert len(sell_signals) >= 1
        assert len(buy_signals) >= 2  # 첫 진입 + 재진입


# ── 지표 컬럼 ────────────────────────────────────────────────────────────────

class TestGetIndicators:
    def test_indicator_columns_exist(self):
        s = default_strategy()
        prices = [100.0, 90.0, 99.0, 110.0, 98.0]
        result = s.get_indicators(make_df(prices))
        assert "candidate_low" in result.columns
        assert "entry_level" in result.columns

    def test_entry_level_is_low_times_mult(self):
        """entry_level = candidate_low × (1 + entry_pct%)"""
        s = PyramidBreakoutStrategy(entry_pct=10.0)
        prices = [100.0, 90.0, 85.0, 85.0]
        result = s.get_indicators(make_df(prices))
        valid = result.dropna(subset=["candidate_low", "entry_level"])
        assert (
            (valid["entry_level"] - valid["candidate_low"] * 1.1).abs() < 1e-6
        ).all()

    def test_original_df_not_modified(self):
        s = default_strategy()
        prices = [100.0, 90.0, 99.0]
        df = make_df(prices)
        original_cols = set(df.columns)
        s.get_indicators(df)
        assert set(df.columns) == original_cols
