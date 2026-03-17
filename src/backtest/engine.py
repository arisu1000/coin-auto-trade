"""
이벤트 기반 백테스팅 엔진

업비트 수수료(0.05%) + 슬리피지를 반영한 고정밀 시뮬레이션.
종가 체결이 아닌 보수적 체결 가정(매수: 고가 방향, 매도: 저가 방향)으로
곡선 적합(Overfitting)을 방지한다.

롱 전용 전략 (BUY/SELL):
    unit_amount 미설정 → 진입 시 전액 투자, 청산 시 전량 매도.
롱/숏 피라미딩 전략 (BUY/SELL/SHORT/COVER):
    unit_amount 설정 → 매 시그널마다 unit_amount씩 진입.
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
        df = self._strategy.get_indicators(df)
        signals = self._strategy.generate_signals(df)

        # 전략에 unit_amount가 있으면 피라미딩 모드
        unit_amount: float | None = getattr(self._strategy, "unit_amount", None)

        portfolio = Portfolio(initial_capital)
        equity_curve: list[float] = []
        trade_records: list[TradeRecord] = []

        # 롱 포지션 추적
        long_entry_time: datetime | None = None
        long_entry_fee_total: float = 0.0

        # 숏 포지션 추적
        short_entry_time: datetime | None = None
        short_entry_fee_total: float = 0.0

        for i, (timestamp, bar) in enumerate(df.iterrows()):
            current_signal = signals.iloc[i]

            # ── 롱 진입 / 피라미딩 ─────────────────────────────
            if current_signal == TradingSignal.BUY:
                # unit_amount 모드: 포지션 있어도 피라미딩 허용
                # all-in 모드: 포지션 없을 때만 진입
                can_buy = (unit_amount is not None) or (not portfolio.has_long_position)
                if can_buy and portfolio.cash > 0:
                    exec_price = self._slippage.buy_price(bar)
                    size = unit_amount if unit_amount is not None else portfolio.cash
                    size = min(size, portfolio.cash)
                    qty = (size - self._fee.calculate(size)) / exec_price
                    fee_amount = self._fee.calculate(exec_price * qty)
                    if qty > 0:
                        if long_entry_time is None:
                            long_entry_time = timestamp
                            long_entry_fee_total = 0.0
                        portfolio.enter_long(exec_price, qty, fee_amount)
                        long_entry_fee_total += fee_amount

            # ── 롱 청산 ────────────────────────────────────────
            elif current_signal == TradingSignal.SELL and portfolio.has_long_position:
                exec_price = self._slippage.sell_price(bar)
                avg_entry = portfolio.long_avg_price
                qty_closed = portfolio.long_qty
                fee_amount = self._fee.calculate(exec_price * qty_closed)
                pnl = portfolio.exit_long(exec_price, fee_amount)
                trade_records.append(TradeRecord(
                    entry_time=long_entry_time or timestamp,
                    exit_time=timestamp,
                    side="long",
                    entry_price=avg_entry,
                    exit_price=exec_price,
                    quantity=qty_closed,
                    pnl=pnl,
                    pnl_pct=(exec_price - avg_entry) / avg_entry * 100 if avg_entry else 0.0,
                    fee_total=long_entry_fee_total + fee_amount,
                ))
                long_entry_time = None
                long_entry_fee_total = 0.0

            # ── 숏 진입 / 피라미딩 ─────────────────────────────
            elif current_signal == TradingSignal.SHORT:
                can_short = (unit_amount is not None) or (not portfolio.has_short_position)
                if can_short and portfolio.cash > 0:
                    exec_price = self._slippage.sell_price(bar)  # 숏은 매도 방향
                    size = unit_amount if unit_amount is not None else portfolio.cash
                    size = min(size, portfolio.cash)
                    qty = (size - self._fee.calculate(size)) / exec_price
                    fee_amount = self._fee.calculate(exec_price * qty)
                    if qty > 0:
                        if short_entry_time is None:
                            short_entry_time = timestamp
                            short_entry_fee_total = 0.0
                        portfolio.enter_short(exec_price, qty, fee_amount)
                        short_entry_fee_total += fee_amount

            # ── 숏 청산 ────────────────────────────────────────
            elif current_signal == TradingSignal.COVER and portfolio.has_short_position:
                exec_price = self._slippage.buy_price(bar)  # 숏 청산은 매수 방향
                avg_short = portfolio.short_avg_price
                qty_short = portfolio.short_qty
                fee_amount = self._fee.calculate(exec_price * qty_short)
                pnl = portfolio.exit_short(exec_price, fee_amount)
                trade_records.append(TradeRecord(
                    entry_time=short_entry_time or timestamp,
                    exit_time=timestamp,
                    side="short",
                    entry_price=avg_short,
                    exit_price=exec_price,
                    quantity=qty_short,
                    pnl=pnl,
                    pnl_pct=(avg_short - exec_price) / avg_short * 100 if avg_short else 0.0,
                    fee_total=short_entry_fee_total + fee_amount,
                ))
                short_entry_time = None
                short_entry_fee_total = 0.0

            equity_curve.append(portfolio.total_equity(bar["close"]))

        return BacktestResult(
            equity_curve=equity_curve,
            trade_records=trade_records,
            initial_capital=initial_capital,
            final_capital=portfolio.total_equity(df.iloc[-1]["close"]),
        )
