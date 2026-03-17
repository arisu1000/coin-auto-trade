"""
롱/숏 피라미딩 전용 백테스트 엔진

기존 BacktestEngine(롱 전용)과 완전히 독립적으로 동작한다.
unit_amount 단위로 여러 번 진입(피라미딩)하고, SELL/COVER로 전량 청산한다.

숏 포지션 자금 모델 (1:1 마진):
    진입: unit_amount를 증거금으로 차감, qty = unit_amount / price 코인 숏
    청산: 증거금 회수 + (entry_price - exit_price) * qty 손익 반영
    total_equity = 현금 + 롱평가액 + (숏평균가 - 현재가) * 숏수량
"""
from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

from src.backtest.fees import FeeSchedule
from src.backtest.report import BacktestReport
from src.backtest.slippage import SlippageModel
from src.strategy.base import Strategy


# ── 시그널 상수 (기존 TradingSignal 건드리지 않음) ─────────────────────────
HOLD  =  0
BUY   =  1   # 롱 진입 / 롱 피라미딩
SELL  = -1   # 롱 청산
SHORT = -2   # 숏 진입 / 숏 피라미딩
COVER =  2   # 숏 청산


@dataclass
class LSTradeRecord:
    entry_time: datetime
    exit_time: datetime | None
    side: str          # "long" | "short"
    entry_price: float
    exit_price: float
    quantity: float
    pnl: float
    pnl_pct: float
    fee_total: float


@dataclass
class LSBacktestResult:
    equity_curve: list[float]
    trade_records: list[LSTradeRecord]
    initial_capital: float
    final_capital: float
    report: "BacktestReport" = field(default=None)  # type: ignore

    def __post_init__(self) -> None:
        # BacktestReport는 pnl/entry_time/exit_time 속성만 참조하므로 그대로 사용 가능
        self.report = BacktestReport(
            equity_curve=self.equity_curve,
            trade_records=self.trade_records,
            initial_capital=self.initial_capital,
        )


class _LSPortfolio:
    """롱/숏 모두 지원하는 내부 포트폴리오 (engine_ls 전용)"""

    def __init__(self, initial_capital: float) -> None:
        self._cash = initial_capital
        # 롱
        self._long_qty: float = 0.0
        self._long_avg: float = 0.0
        # 숏
        self._short_qty: float = 0.0
        self._short_avg: float = 0.0

    @property
    def cash(self) -> float:
        return self._cash

    @property
    def has_long(self) -> bool:
        return self._long_qty > 0

    @property
    def has_short(self) -> bool:
        return self._short_qty > 0

    @property
    def long_qty(self) -> float:
        return self._long_qty

    @property
    def long_avg(self) -> float:
        return self._long_avg

    @property
    def short_qty(self) -> float:
        return self._short_qty

    @property
    def short_avg(self) -> float:
        return self._short_avg

    def enter_long(self, price: float, unit_amount: float, fee: float) -> float:
        """unit_amount만큼 롱 진입 (피라미딩 포함). 실제 매수 수량 반환."""
        size = min(unit_amount, self._cash)
        qty = (size - fee) / price
        if qty <= 0:
            return 0.0
        actual_fee = fee
        cost = price * qty + actual_fee
        self._cash -= cost
        total = self._long_qty + qty
        self._long_avg = (self._long_avg * self._long_qty + price * qty) / total
        self._long_qty = total
        return qty

    def exit_long(self, price: float, fee: float) -> float:
        """롱 전량 청산. PnL 반환."""
        gross = price * self._long_qty
        pnl = gross - fee - self._long_avg * self._long_qty
        self._cash += gross - fee
        self._long_qty = 0.0
        self._long_avg = 0.0
        return pnl

    def enter_short(self, price: float, unit_amount: float, fee: float) -> float:
        """unit_amount만큼 숏 진입 (피라미딩 포함). 실제 매도 수량 반환."""
        size = min(unit_amount, self._cash)
        qty = (size - fee) / price
        if qty <= 0:
            return 0.0
        margin = price * qty + fee   # 증거금 차감
        self._cash -= margin
        total = self._short_qty + qty
        self._short_avg = (self._short_avg * self._short_qty + price * qty) / total
        self._short_qty = total
        return qty

    def exit_short(self, price: float, fee: float) -> float:
        """숏 전량 청산. PnL 반환."""
        committed = self._short_avg * self._short_qty
        gross_pnl = (self._short_avg - price) * self._short_qty
        self._cash += committed + gross_pnl - fee
        pnl = gross_pnl - fee
        self._short_qty = 0.0
        self._short_avg = 0.0
        return pnl

    def total_equity(self, current_price: float) -> float:
        long_val = self._long_qty * current_price
        short_unrealized = (self._short_avg - current_price) * self._short_qty
        return self._cash + long_val + short_unrealized


class LSBacktestEngine:
    """
    롱/숏 피라미딩 전용 백테스트 엔진

    전략은 BUY(1) / SELL(-1) / SHORT(-2) / COVER(2) / HOLD(0) 시그널을 반환해야 한다.
    전략에 unit_amount 속성이 있어야 한다.

    사용법:
        engine = LSBacktestEngine(strategy, slippage, fee)
        result = engine.run(df, initial_capital=1_000_000)
        print(result.report.summary_text())
    """

    def __init__(self, strategy: Strategy, slippage: SlippageModel, fee: FeeSchedule) -> None:
        self._strategy = strategy
        self._slippage = slippage
        self._fee = fee

    def run(self, df: pd.DataFrame, initial_capital: float = 1_000_000) -> LSBacktestResult:
        df = df.copy().sort_index()
        df = self._strategy.get_indicators(df)
        signals = self._strategy.generate_signals(df)

        unit_amount: float = getattr(self._strategy, "unit_amount", initial_capital)

        portfolio = _LSPortfolio(initial_capital)
        equity_curve: list[float] = []
        trade_records: list[LSTradeRecord] = []

        long_entry_time: datetime | None = None
        long_entry_fees: float = 0.0
        short_entry_time: datetime | None = None
        short_entry_fees: float = 0.0

        for i, (timestamp, bar) in enumerate(df.iterrows()):
            sig = int(signals.iloc[i])

            # ── 롱 진입 / 피라미딩 ─────────────────────────────
            if sig == BUY and portfolio.cash > 0:
                exec_price = self._slippage.buy_price(bar)
                fee_amount = self._fee.calculate(unit_amount)
                qty = portfolio.enter_long(exec_price, unit_amount, fee_amount)
                if qty > 0:
                    if long_entry_time is None:
                        long_entry_time = timestamp
                        long_entry_fees = 0.0
                    long_entry_fees += fee_amount

            # ── 롱 청산 ────────────────────────────────────────
            elif sig == SELL and portfolio.has_long:
                exec_price = self._slippage.sell_price(bar)
                avg_entry = portfolio.long_avg
                qty_closed = portfolio.long_qty
                fee_amount = self._fee.calculate(exec_price * qty_closed)
                pnl = portfolio.exit_long(exec_price, fee_amount)
                trade_records.append(LSTradeRecord(
                    entry_time=long_entry_time or timestamp,
                    exit_time=timestamp,
                    side="long",
                    entry_price=avg_entry,
                    exit_price=exec_price,
                    quantity=qty_closed,
                    pnl=pnl,
                    pnl_pct=(exec_price - avg_entry) / avg_entry * 100 if avg_entry else 0.0,
                    fee_total=long_entry_fees + fee_amount,
                ))
                long_entry_time = None
                long_entry_fees = 0.0

            # ── 숏 진입 / 피라미딩 ─────────────────────────────
            elif sig == SHORT and portfolio.cash > 0:
                exec_price = self._slippage.sell_price(bar)
                fee_amount = self._fee.calculate(unit_amount)
                qty = portfolio.enter_short(exec_price, unit_amount, fee_amount)
                if qty > 0:
                    if short_entry_time is None:
                        short_entry_time = timestamp
                        short_entry_fees = 0.0
                    short_entry_fees += fee_amount

            # ── 숏 청산 ────────────────────────────────────────
            elif sig == COVER and portfolio.has_short:
                exec_price = self._slippage.buy_price(bar)
                avg_short = portfolio.short_avg
                qty_short = portfolio.short_qty
                fee_amount = self._fee.calculate(exec_price * qty_short)
                pnl = portfolio.exit_short(exec_price, fee_amount)
                trade_records.append(LSTradeRecord(
                    entry_time=short_entry_time or timestamp,
                    exit_time=timestamp,
                    side="short",
                    entry_price=avg_short,
                    exit_price=exec_price,
                    quantity=qty_short,
                    pnl=pnl,
                    pnl_pct=(avg_short - exec_price) / avg_short * 100 if avg_short else 0.0,
                    fee_total=short_entry_fees + fee_amount,
                ))
                short_entry_time = None
                short_entry_fees = 0.0

            equity_curve.append(portfolio.total_equity(bar["close"]))

        return LSBacktestResult(
            equity_curve=equity_curve,
            trade_records=trade_records,
            initial_capital=initial_capital,
            final_capital=portfolio.total_equity(df.iloc[-1]["close"]),
        )
