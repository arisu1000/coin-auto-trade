"""
백테스트 엔진 단위 테스트

수수료, 슬리피지, 성과 지표 계산 검증
"""
import pandas as pd
import pytest

from src.backtest.engine import BacktestEngine
from src.backtest.fees import FeeSchedule
from src.backtest.report import BacktestReport
from src.backtest.slippage import ConservativeSlippage, FixedBpsSlippage
from src.strategy.base import TradingSignal
from src.strategy.mean_reversion import MeanReversionStrategy
from src.strategy.momentum import MomentumStrategy


@pytest.fixture
def simple_df() -> pd.DataFrame:
    """단순 상승 후 하락 패턴"""
    n = 100
    closes = [10_000 + i * 100 for i in range(50)] + [14_900 - i * 100 for i in range(50)]
    return pd.DataFrame({
        "open":   [c - 50 for c in closes],
        "high":   [c + 100 for c in closes],
        "low":    [c - 100 for c in closes],
        "close":  closes,
        "volume": [1.0] * n,
    }, index=pd.date_range("2024-01-01", periods=n, freq="1min"))


class TestSlippageModels:
    def test_fixed_bps_buy_higher_than_close(self, simple_df):
        """고정 bps 매수가격 > 종가"""
        model = FixedBpsSlippage(bps=3)
        bar = simple_df.iloc[0]
        buy_price = model.buy_price(bar)
        assert buy_price > float(bar["close"])

    def test_fixed_bps_sell_lower_than_close(self, simple_df):
        """고정 bps 매도가격 < 종가"""
        model = FixedBpsSlippage(bps=3)
        bar = simple_df.iloc[0]
        sell_price = model.sell_price(bar)
        assert sell_price < float(bar["close"])

    def test_conservative_buy_between_close_and_high(self, simple_df):
        """보수적 매수가격은 종가와 고가 사이"""
        model = ConservativeSlippage()
        bar = simple_df.iloc[0]
        buy_price = model.buy_price(bar)
        assert float(bar["close"]) <= buy_price <= float(bar["high"])

    def test_conservative_sell_between_low_and_close(self, simple_df):
        """보수적 매도가격은 저가와 종가 사이"""
        model = ConservativeSlippage()
        bar = simple_df.iloc[0]
        sell_price = model.sell_price(bar)
        assert float(bar["low"]) <= sell_price <= float(bar["close"])


class TestFeeSchedule:
    def test_fee_calculation(self):
        """수수료 계산 (0.05% = 5 bps)"""
        fee = FeeSchedule(rate_bps=5)
        assert fee.calculate(1_000_000) == pytest.approx(500.0, rel=1e-6)

    def test_fee_rate_property(self):
        fee = FeeSchedule(rate_bps=5)
        assert fee.rate == pytest.approx(0.0005)


class TestBacktestReport:
    def _make_report(self, equity_curve: list[float]) -> BacktestReport:
        return BacktestReport(
            equity_curve=equity_curve,
            trade_records=[],
            initial_capital=equity_curve[0],
        )

    def test_total_return_positive(self):
        report = self._make_report([1_000_000, 1_100_000, 1_200_000])
        assert report.total_return_pct() == pytest.approx(20.0, rel=0.01)

    def test_total_return_negative(self):
        report = self._make_report([1_000_000, 900_000, 800_000])
        assert report.total_return_pct() == pytest.approx(-20.0, rel=0.01)

    def test_max_drawdown(self):
        """MDD: 최고점 대비 최악의 하락"""
        # 1M → 1.2M (고점) → 0.9M (25% 하락)
        report = self._make_report([1_000_000, 1_100_000, 1_200_000, 900_000])
        mdd = report.max_drawdown_pct()
        assert mdd == pytest.approx(25.0, rel=0.1)

    def test_sharpe_no_variance(self):
        """변동 없는 수익률의 샤프 지수"""
        report = self._make_report([1_000_000] * 100)
        assert report.sharpe_ratio() == 0.0

    def test_summary_keys(self):
        report = self._make_report([1_000_000, 1_050_000, 1_100_000])
        summary = report.summary()
        required_keys = [
            "total_return_pct", "max_drawdown_pct", "sharpe_ratio",
            "win_rate_pct", "profit_factor", "total_trades",
        ]
        for key in required_keys:
            assert key in summary, f"요약에 '{key}' 키 없음"


class TestBacktestEngineIntegration:
    def test_run_returns_result(self, sample_ohlcv_df):
        """백테스트 실행 후 결과 반환"""
        strategy = MomentumStrategy()
        engine = BacktestEngine(
            strategy=strategy,
            slippage=FixedBpsSlippage(bps=3),
            fee=FeeSchedule(rate_bps=5),
        )
        result = engine.run(sample_ohlcv_df, initial_capital=1_000_000)
        assert len(result.equity_curve) > 0
        assert result.initial_capital == 1_000_000

    def test_equity_curve_length_matches_data(self, sample_ohlcv_df):
        """자산 곡선 길이 = 데이터 행 수"""
        strategy = MeanReversionStrategy()
        engine = BacktestEngine(
            strategy=strategy,
            slippage=FixedBpsSlippage(bps=3),
            fee=FeeSchedule(rate_bps=5),
        )
        result = engine.run(sample_ohlcv_df, initial_capital=1_000_000)
        assert len(result.equity_curve) == len(sample_ohlcv_df)

    def test_final_capital_reasonable(self, sample_ohlcv_df):
        """최종 자산이 0 이상"""
        strategy = MomentumStrategy()
        engine = BacktestEngine(
            strategy=strategy,
            slippage=ConservativeSlippage(),
            fee=FeeSchedule(rate_bps=5),
        )
        result = engine.run(sample_ohlcv_df, initial_capital=1_000_000)
        assert result.final_capital > 0
