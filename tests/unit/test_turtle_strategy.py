"""
터틀 트레이딩 전략 단위 테스트

검증 항목:
- 돈치안 채널 돌파 시 매수 시그널 발생
- exit_period 저가 이탈 시 매도 시그널 발생
- ATR 기반 손절가 이탈 시 매도 시그널 발생
- Look-ahead Bias 없음 (이전 봉 채널만 참조)
- System 1 / System 2 파라미터 분기
- ATR 포지션 사이징 계산
- 파라미터 유효성 검사
"""

import numpy as np
import pandas as pd
import pytest

from src.strategy.base import TradingSignal
from src.strategy.turtle import TurtleStrategy, _rolling_max, _rolling_min


# ── 헬퍼 ─────────────────────────────────────────────────────────────

def make_flat_df(n: int = 100, price: float = 50_000_000) -> pd.DataFrame:
    """가격 변동 없는 횡보 데이터 (모든 시그널 = HOLD 기대)"""
    return pd.DataFrame({
        "open":   [price] * n,
        "high":   [price * 1.001] * n,
        "low":    [price * 0.999] * n,
        "close":  [price] * n,
        "volume": [1.0] * n,
    }, index=pd.date_range("2024-01-01", periods=n, freq="1D"))


def make_breakout_df(
    flat_periods: int = 30,
    breakout_price: float = 55_000_000,
    base_price: float = 50_000_000,
) -> pd.DataFrame:
    """
    flat_periods 동안 횡보 후 급등하는 데이터.
    entry_period=20 기준으로 flat_periods > 20이면 돌파 시그널 발생.
    """
    n = flat_periods + 20
    prices = [base_price] * flat_periods + [breakout_price] * 20
    return pd.DataFrame({
        "open":   [p * 0.999 for p in prices],
        "high":   [p * 1.002 for p in prices],
        "low":    [p * 0.998 for p in prices],
        "close":  prices,
        "volume": [1.0] * n,
    }, index=pd.date_range("2024-01-01", periods=n, freq="1D"))


def make_trend_then_crash_df(
    flat_periods: int = 25,
    spike_periods: int = 5,
    crash_periods: int = 30,
) -> pd.DataFrame:
    """
    횡보 → 급등(돌파) → 급락 시나리오

    1. flat_periods 동안 base_price에서 횡보 (채널 형성)
    2. spike_periods 동안 급등 → 돈치안 상단 돌파 → 매수 시그널 발생
    3. crash_periods 동안 급락 → exit 채널 이탈 또는 ATR 손절 → 매도 시그널
    """
    base = 50_000_000
    spike_top = base * 1.15          # 15% 급등
    flat_prices  = [base] * flat_periods
    spike_prices = [base + (spike_top - base) * (i + 1) / spike_periods
                    for i in range(spike_periods)]
    # 급락: spike_top → base 아래로
    crash_target = base * 0.85
    crash_prices = [spike_top - (spike_top - crash_target) * (i + 1) / crash_periods
                    for i in range(crash_periods)]
    prices = flat_prices + spike_prices + crash_prices
    n = len(prices)

    return pd.DataFrame({
        "open":   [p * 0.999 for p in prices],
        "high":   [p * 1.002 for p in prices],
        "low":    [p * 0.998 for p in prices],
        "close":  prices,
        "volume": [1.0] * n,
    }, index=pd.date_range("2024-01-01", periods=n, freq="1D"))


# ── 내부 유틸리티 테스트 ──────────────────────────────────────────────

class TestRollingHelpers:
    def test_rolling_max_no_lookahead(self):
        """i번째 값은 arr[i-window:i]의 최댓값 (arr[i] 미포함)"""
        arr = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        result = _rolling_max(arr, window=3)
        # i=3: arr[0:3] = [1,2,3] → max=3 (arr[3]=4 미포함)
        assert result[3] == 3.0
        # i=4: arr[1:4] = [2,3,4] → max=4 (arr[4]=5 미포함)
        assert result[4] == 4.0

    def test_rolling_max_warmup_is_nan(self):
        arr = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        result = _rolling_max(arr, window=3)
        assert np.isnan(result[0])
        assert np.isnan(result[1])
        assert np.isnan(result[2])

    def test_rolling_min_no_lookahead(self):
        arr = np.array([5.0, 4.0, 3.0, 2.0, 1.0])
        result = _rolling_min(arr, window=3)
        # i=3: arr[0:3] = [5,4,3] → min=3 (arr[3]=2 미포함)
        assert result[3] == 3.0

    def test_rolling_min_warmup_is_nan(self):
        arr = np.ones(10)
        result = _rolling_min(arr, window=5)
        for i in range(5):
            assert np.isnan(result[i])


# ── 초기화 및 파라미터 검증 ───────────────────────────────────────────

class TestTurtleInit:
    def test_system1_defaults(self):
        s = TurtleStrategy(system=1)
        assert s.entry_period == 20
        assert s.exit_period == 10

    def test_system2_defaults(self):
        s = TurtleStrategy(system=2)
        assert s.entry_period == 55
        assert s.exit_period == 20

    def test_custom_periods(self):
        s = TurtleStrategy(entry_period=30, exit_period=10)
        assert s.entry_period == 30
        assert s.exit_period == 10

    def test_exit_must_be_less_than_entry(self):
        with pytest.raises(ValueError, match="exit_period"):
            TurtleStrategy(entry_period=10, exit_period=20)

    def test_invalid_system(self):
        with pytest.raises(ValueError, match="system"):
            TurtleStrategy(system=3)

    def test_validate_params_valid(self):
        s = TurtleStrategy()
        assert s.validate_params({"entry_period": 20, "exit_period": 10, "atr_period": 20, "stop_atr_mult": 2.0})

    def test_validate_params_exit_ge_entry(self):
        s = TurtleStrategy()
        assert not s.validate_params({"entry_period": 10, "exit_period": 10})

    def test_validate_params_zero_stop(self):
        s = TurtleStrategy()
        assert not s.validate_params({"entry_period": 20, "exit_period": 10, "stop_atr_mult": 0})

    def test_repr(self):
        s = TurtleStrategy(system=1)
        assert "TurtleStrategy" in repr(s)
        assert "entry=20" in repr(s)


# ── 시그널 생성 ───────────────────────────────────────────────────────

class TestTurtleSignals:
    def test_flat_market_no_signals(self):
        """횡보장에서 매수/매도 시그널 없음"""
        s = TurtleStrategy(system=1)
        df = make_flat_df(n=60)
        signals = s.generate_signals(df)
        assert (signals != TradingSignal.HOLD).sum() == 0

    def test_breakout_generates_buy(self):
        """돈치안 채널 상단 돌파 → 매수 시그널"""
        s = TurtleStrategy(system=1)  # entry_period=20
        df = make_breakout_df(flat_periods=25, breakout_price=55_000_000)
        signals = s.generate_signals(df)
        buy_signals = signals[signals == TradingSignal.BUY]
        assert len(buy_signals) >= 1, "돌파 후 매수 시그널이 없음"

    def test_buy_signal_only_after_warmup(self):
        """워밍업 기간(entry_period) 이전에는 매수 없음"""
        s = TurtleStrategy(system=1)  # entry_period=20
        df = make_breakout_df(flat_periods=25)
        signals = s.generate_signals(df)
        # 처음 20개 봉에서 매수 없어야 함
        assert (signals.iloc[:20] == TradingSignal.BUY).sum() == 0

    def test_crash_after_entry_generates_sell(self):
        """진입 후 exit 채널 이탈 → 매도 시그널"""
        s = TurtleStrategy(system=1)
        df = make_trend_then_crash_df()
        signals = s.generate_signals(df)
        sell_signals = signals[signals == TradingSignal.SELL]
        assert len(sell_signals) >= 1, "급락 후 매도 시그널이 없음"

    def test_no_signal_before_first_buy(self):
        """매수 진입 전에는 매도 시그널 없음"""
        s = TurtleStrategy(system=1)
        df = make_flat_df(n=60)
        signals = s.generate_signals(df)
        assert (signals == TradingSignal.SELL).sum() == 0

    def test_no_consecutive_buys_without_sell(self):
        """포지션 보유 중 매수 시그널 중복 없음"""
        s = TurtleStrategy(system=1)
        df = make_breakout_df(flat_periods=25, breakout_price=55_000_000)
        signals = s.generate_signals(df)
        buy_indices = signals[signals == TradingSignal.BUY].index.tolist()
        # 연속 매수가 없는지 확인 (같은 position에서 두 번 매수 안 함)
        for i in range(len(buy_indices) - 1):
            between = signals.loc[buy_indices[i]:buy_indices[i + 1]]
            assert (between == TradingSignal.SELL).sum() >= 1 or len(buy_indices) == 1

    def test_system2_uses_longer_periods(self):
        """System 2는 더 긴 채널 → System 1보다 진입 신호 적음"""
        df = make_breakout_df(flat_periods=60, breakout_price=55_000_000)
        s1 = TurtleStrategy(system=1)
        s2 = TurtleStrategy(system=2)
        sig1 = s1.generate_signals(df)
        sig2 = s2.generate_signals(df)
        # System 2는 55일 채널 → 60일 이상 데이터 필요, 신호 수 ≤ System 1
        buy1 = (sig1 == TradingSignal.BUY).sum()
        buy2 = (sig2 == TradingSignal.BUY).sum()
        assert buy2 <= buy1


# ── ATR 손절 ──────────────────────────────────────────────────────────

class TestStopLoss:
    def test_atr_stop_triggers_sell(self):
        """ATR 2배 손실 시 강제 청산"""
        # 진입 후 즉각 급락 (2N 이상) 시나리오
        n = 60
        prices = [50_000_000] * 30 + [55_000_000] + [48_000_000] * 29  # 진입 후 급락
        df = pd.DataFrame({
            "open":   [p * 0.999 for p in prices],
            "high":   [p * 1.002 for p in prices],
            "low":    [p * 0.995 for p in prices],
            "close":  prices,
            "volume": [1.0] * n,
        }, index=pd.date_range("2024-01-01", periods=n, freq="1D"))

        s = TurtleStrategy(system=1, stop_atr_mult=2.0)
        signals = s.generate_signals(df)
        # 급락 구간에서 매도 발생해야 함
        sell_count = (signals == TradingSignal.SELL).sum()
        assert sell_count >= 1


# ── 지표 컬럼 ─────────────────────────────────────────────────────────

class TestGetIndicators:
    def test_indicator_columns_exist(self, sample_ohlcv_df):
        s = TurtleStrategy()
        result = s.get_indicators(sample_ohlcv_df)
        expected_cols = ["donchian_upper", "donchian_lower", "donchian_exit", "atr", "stop_price", "channel_width_pct"]
        for col in expected_cols:
            assert col in result.columns, f"컬럼 '{col}' 없음"

    def test_donchian_upper_ge_lower(self, sample_ohlcv_df):
        """상단 채널 ≥ 하단 채널"""
        s = TurtleStrategy()
        result = s.get_indicators(sample_ohlcv_df)
        valid = result.dropna(subset=["donchian_upper", "donchian_lower"])
        assert (valid["donchian_upper"] >= valid["donchian_lower"]).all()

    def test_atr_positive(self, sample_ohlcv_df):
        """ATR은 양수"""
        s = TurtleStrategy()
        result = s.get_indicators(sample_ohlcv_df)
        valid_atr = result["atr"].dropna()
        assert (valid_atr > 0).all()

    def test_stop_price_below_close(self, sample_ohlcv_df):
        """손절가 < 현재가 (stop_atr_mult > 0이면 항상)"""
        s = TurtleStrategy(stop_atr_mult=2.0)
        result = s.get_indicators(sample_ohlcv_df)
        valid = result.dropna(subset=["stop_price"])
        assert (valid["stop_price"] < valid["close"]).all()

    def test_original_df_not_modified(self, sample_ohlcv_df):
        """원본 DataFrame이 변경되지 않음"""
        s = TurtleStrategy()
        original_cols = set(sample_ohlcv_df.columns)
        s.get_indicators(sample_ohlcv_df)
        assert set(sample_ohlcv_df.columns) == original_cols


# ── ATR 포지션 사이징 ──────────────────────────────────────────────────

class TestPositionSizing:
    def test_unit_size_positive(self):
        s = TurtleStrategy()
        unit = s.calc_unit_size(
            account_balance=1_000_000,
            current_price=50_000_000,
            atr=500_000,
            risk_pct=1.0,
        )
        # 기대값: (1,000,000 * 0.01) / 500,000 = 0.02 BTC
        assert unit > 0
        assert abs(unit - 0.02) < 1e-6

    def test_unit_size_zero_on_zero_atr(self):
        s = TurtleStrategy()
        unit = s.calc_unit_size(1_000_000, 50_000_000, atr=0)
        assert unit == 0.0

    def test_unit_size_zero_on_zero_price(self):
        s = TurtleStrategy()
        unit = s.calc_unit_size(1_000_000, 0, atr=500_000)
        assert unit == 0.0

    def test_higher_risk_pct_gives_larger_unit(self):
        s = TurtleStrategy()
        unit_1pct = s.calc_unit_size(1_000_000, 50_000_000, 500_000, risk_pct=1.0)
        unit_2pct = s.calc_unit_size(1_000_000, 50_000_000, 500_000, risk_pct=2.0)
        assert unit_2pct > unit_1pct

    def test_higher_atr_gives_smaller_unit(self):
        """변동성이 클수록 유닛 수량 감소 (리스크 일정 유지)"""
        s = TurtleStrategy()
        unit_low_atr  = s.calc_unit_size(1_000_000, 50_000_000, atr=200_000)
        unit_high_atr = s.calc_unit_size(1_000_000, 50_000_000, atr=800_000)
        assert unit_low_atr > unit_high_atr
