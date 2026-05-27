"""
N봉 고점 돌파 전략 (Donchian Breakout)

── 진입 ─────────────────────────────────────────────────────────────────────
종가가 직전 N봉(현재봉 제외)의 최고 종가를 돌파하면 매수.
추세 초입에 탑승해 빠르게 수익을 확정하는 단기 전략.

── 청산 (먼저 발생하는 조건 적용) ──────────────────────────────────────────
  1. 손절       : 저가 <= 진입가 × (1 - stop_pct%)
  2. 고정 익절  : 고가 >= 진입가 × (1 + profit_pct%)   (profit_pct=0이면 비활성)
  3. 트레일링   : 저가 <= 최고가 × (1 - trail_pct%)    (trail_pct=0이면 비활성)

── 파라미터 ─────────────────────────────────────────────────────────────────
  window      : 돌파 기준 N봉 수 (기본 20)
  stop_pct    : 손절률 % (기본 3.0)
  profit_pct  : 고정 익절률 % (기본 0 = 비활성)
  trail_pct   : 트레일링 스탑 % (기본 3.0, 0 = 비활성)
  unit_amount : 1회 투입 금액 원 (기본 100,000)
"""

import numpy as np
import pandas as pd

from src.strategy.base import Strategy, TradingSignal


class BreakoutNStrategy(Strategy):
    """N봉 고점 돌파 전략"""

    name = "breakout_n"

    def __init__(
        self,
        window: int = 20,
        stop_pct: float = 3.0,
        profit_pct: float = 0.0,
        trail_pct: float = 3.0,
        unit_amount: float = 100_000.0,
    ) -> None:
        window = int(window)
        if window < 2:
            raise ValueError(f"window는 2 이상이어야 합니다 (현재: {window})")
        if not (0 < stop_pct < 100):
            raise ValueError(f"stop_pct은 0 초과 100 미만이어야 합니다 (현재: {stop_pct})")
        if not (0 <= profit_pct < 100):
            raise ValueError(f"profit_pct은 0 이상 100 미만이어야 합니다 (현재: {profit_pct})")
        if not (0 <= trail_pct < 100):
            raise ValueError(f"trail_pct은 0 이상 100 미만이어야 합니다 (현재: {trail_pct})")
        if unit_amount <= 0:
            raise ValueError(f"unit_amount는 0보다 커야 합니다 (현재: {unit_amount})")

        self.window = window
        self.stop_pct = stop_pct
        self.profit_pct = profit_pct
        self.trail_pct = trail_pct
        self.unit_amount = unit_amount

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        close = df["close"].values.astype(float)
        high  = df["high"].values.astype(float)
        low   = df["low"].values.astype(float)
        n = len(df)

        signals = pd.Series(TradingSignal.HOLD, index=df.index, dtype=int)

        in_position = False
        entry_price = 0.0
        highest_price = 0.0

        stop_mult   = 1 - self.stop_pct   / 100
        profit_mult = 1 + self.profit_pct / 100
        trail_mult  = 1 - self.trail_pct  / 100

        for i in range(self.window, n):
            if not in_position:
                prev_high = close[i - self.window : i].max()
                if close[i] > prev_high:
                    signals.iloc[i] = TradingSignal.BUY
                    in_position = True
                    entry_price = close[i]
                    highest_price = high[i]
            else:
                if high[i] > highest_price:
                    highest_price = high[i]

                # 손절
                if low[i] <= entry_price * stop_mult:
                    signals.iloc[i] = TradingSignal.SELL
                    in_position = False
                # 고정 익절 (profit_pct > 0일 때만)
                elif self.profit_pct > 0 and high[i] >= entry_price * profit_mult:
                    signals.iloc[i] = TradingSignal.SELL
                    in_position = False
                # 트레일링 스탑 (trail_pct > 0일 때만)
                elif self.trail_pct > 0 and low[i] <= highest_price * trail_mult:
                    signals.iloc[i] = TradingSignal.SELL
                    in_position = False

        return signals

    def get_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        result = df.copy()
        # 직전 N봉 최고 종가 (진입 트리거 기준선)
        result["donchian_high"] = df["close"].shift(1).rolling(self.window).max()
        return result

    def validate_params(self, params: dict) -> bool:
        try:
            window      = int(params.get("window",      self.window))
            stop_pct    = float(params.get("stop_pct",    self.stop_pct))
            profit_pct  = float(params.get("profit_pct",  self.profit_pct))
            trail_pct   = float(params.get("trail_pct",   self.trail_pct))
            unit_amount = float(params.get("unit_amount", self.unit_amount))
            return (
                window >= 2
                and 0 < stop_pct < 100
                and 0 <= profit_pct < 100
                and 0 <= trail_pct < 100
                and unit_amount > 0
            )
        except (TypeError, ValueError):
            return False

    def __repr__(self) -> str:
        return (
            f"BreakoutNStrategy("
            f"window={self.window}, stop={self.stop_pct}%, "
            f"profit={self.profit_pct}%, trail={self.trail_pct}%, "
            f"unit={self.unit_amount:,.0f}원)"
        )
