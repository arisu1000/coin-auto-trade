"""
이벤트 기반 백테스팅 엔진

업비트 수수료(0.05%) + 슬리피지를 반영한 고정밀 시뮬레이션.
종가 체결이 아닌 보수적 체결 가정(매수: 고가 방향, 매도: 저가 방향)으로
곡선 적합(Overfitting)을 방지한다.
"""
from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

from src.backtest.fees import FeeSchedule
from src.backtest.portfolio import Portfolio
from src.backtest.report import BacktestReport
from src.backtest.slippage import SlippageModel
from src.strategy.base import Strategy, TradingSignal


@dataclass
class TradeRecord:
    """개별 매매 내역"""
    entry_time: datetime
    exit_time: datetime | None
    side: str              # "long" | "short"
    entry_price: float
    exit_price: float
    quantity: float
    pnl: float
    pnl_pct: float
    fee_total: float


@dataclass
class BacktestResult:
    """백테스트 결과 집계"""
    equity_curve: list[float]
    trade_records: list[TradeRecord]
    initial_capital: float
    final_capital: float
    report: "BacktestReport" = field(default=None)  # type: ignore

    def __post_init__(self) -> None:
        self.report = BacktestReport(
            equity_curve=self.equity_curve,
            trade_records=self.trade_records,
            initial_capital=self.initial_capital,
        )


class BacktestEngine:
    """
    전략 백테스트 실행기

    사용법:
        engine = BacktestEngine(strategy, slippage, fee)
        result = engine.run(df, initial_capital=1_000_000)
        print(result.report.summary())
    """

    def __init__(
        self,
        strategy: Strategy,
        slippage: SlippageModel,
        fee: FeeSchedule,
    ) -> None:
        self._strategy = strategy
        self._slippage = slippage
        self._fee = fee

    def run(self, df: pd.DataFrame, initial_capital: float = 1_000_000) -> BacktestResult:
        """
        전체 기간 백테스트 실행

        Args:
            df: OHLCV DataFrame (컬럼: open, high, low, close, volume)
            initial_capital: 초기 자본금 (원화)

        Returns:
            BacktestResult (지표 포함)
        """
        df = df.copy().sort_index()

        # 기술적 지표 계산 (전략이 get_indicators를 구현한 경우)
        df = self._strategy.get_indicators(df)

        # 전체 구간에 대한 시그널 일괄 계산
        signals = self._strategy.generate_signals(df)

        portfolio = Portfolio(initial_capital)
        equity_curve: list[float] = []
        trade_records: list[TradeRecord] = []

        entry_time = None
        entry_price = 0.0
        entry_fee = 0.0

        for i, (timestamp, bar) in enumerate(df.iterrows()):
            current_signal = signals.iloc[i]

            # ── 포지션 진입 ────────────────────────────────────
            if current_signal == TradingSignal.BUY and not portfolio.has_position:
                # 보수적 체결: 고가 방향으로 슬리피지 적용
                exec_price = self._slippage.buy_price(bar)
                fee_amount = self._fee.calculate(exec_price * portfolio.max_quantity(exec_price))
                qty = portfolio.max_quantity(exec_price) * (1 - self._fee.rate)

                if qty > 0:
                    portfolio.enter_long(exec_price, qty, fee_amount)
                    entry_time = timestamp
                    entry_price = exec_price
                    entry_fee = fee_amount

            # ── 포지션 청산 ────────────────────────────────────
            elif current_signal == TradingSignal.SELL and portfolio.has_position:
                # 보수적 체결: 저가 방향으로 슬리피지 적용
                exec_price = self._slippage.sell_price(bar)
                fee_amount = self._fee.calculate(exec_price * portfolio.position_size)
                pnl = portfolio.exit_long(exec_price, fee_amount)

                gross_pnl_pct = (exec_price - entry_price) / entry_price * 100
                trade_records.append(TradeRecord(
                    entry_time=entry_time,
                    exit_time=timestamp,
                    side="long",
                    entry_price=entry_price,
                    exit_price=exec_price,
                    quantity=portfolio.position_size,
                    pnl=pnl,
                    pnl_pct=gross_pnl_pct,
                    fee_total=entry_fee + fee_amount,
                ))

            equity_curve.append(portfolio.total_equity(bar["close"]))

        return BacktestResult(
            equity_curve=equity_curve,
            trade_records=trade_records,
            initial_capital=initial_capital,
            final_capital=portfolio.total_equity(df.iloc[-1]["close"]),
        )
