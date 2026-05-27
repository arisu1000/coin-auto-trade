"""
추세 추종 단순 진입/청산 전략 (Trend Follow)

── 진입 ─────────────────────────────────────────────────────────────────────
포지션이 없을 때 직전 저점(running minimum)을 추적한다.
  종가 >= 저점 × (1 + entry_pct%)  →  매수

── 청산 (먼저 발생하는 조건 적용) ──────────────────────────────────────────
  1. 고정 익절  : 고가 >= 진입가 × (1 + profit_pct%)
  2. 손절       : 저가 <= 진입가 × (1 - stop_pct%)
  3. 트레일링   : 저가 <= 최고가 × (1 - trail_pct%)  (trail_pct=0 이면 비활성)

── 파라미터 ─────────────────────────────────────────────────────────────────
  entry_pct   : 저점 대비 진입 상승률 % (기본 10.0)
  profit_pct  : 진입가 대비 익절률 %   (기본 10.0)
  stop_pct    : 진입가 대비 손절률 %   (기본 5.0)
  trail_pct   : 최고가 대비 트레일링 % (기본 0.0 = 비활성)
  unit_amount : 1회 투입 금액 원       (기본 100,000)
"""

import pandas as pd

from src.strategy.base import Strategy, TradingSignal


class TrendFollowStrategy(Strategy):
    """추세 추종 단순 진입/청산 전략"""

    name = "trend_follow"

    def __init__(
        self,
        entry_pct: float = 10.0,
        profit_pct: float = 10.0,
        stop_pct: float = 5.0,
        trail_pct: float = 0.0,
        unit_amount: float = 100_000.0,
    ) -> None:
        if not (0 < entry_pct < 100):
            raise ValueError(f"entry_pct은 0 초과 100 미만이어야 합니다 (현재: {entry_pct})")
        if not (0 < profit_pct < 100):
            raise ValueError(f"profit_pct은 0 초과 100 미만이어야 합니다 (현재: {profit_pct})")
        if not (0 < stop_pct < 100):
            raise ValueError(f"stop_pct은 0 초과 100 미만이어야 합니다 (현재: {stop_pct})")
        if not (0 <= trail_pct < 100):
            raise ValueError(f"trail_pct은 0 이상 100 미만이어야 합니다 (현재: {trail_pct})")
        if unit_amount <= 0:
            raise ValueError(f"unit_amount는 0보다 커야 합니다 (현재: {unit_amount})")

        self.entry_pct = entry_pct
        self.profit_pct = profit_pct
        self.stop_pct = stop_pct
        self.trail_pct = trail_pct
        self.unit_amount = unit_amount

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        close = df["close"].values.astype(float)
        high  = df["high"].values.astype(float)
        low   = df["low"].values.astype(float)
        n = len(df)

        signals = pd.Series(TradingSignal.HOLD, index=df.index, dtype=int)

        in_position   = False
        entry_price   = 0.0
        highest_price = 0.0
        candidate_low = close[0]

        entry_mult  = 1 + self.entry_pct  / 100
        profit_mult = 1 + self.profit_pct / 100
        stop_mult   = 1 - self.stop_pct   / 100
        trail_mult  = 1 - self.trail_pct  / 100

        for i in range(1, n):
            c = close[i]

            if not in_position:
                # 저점 갱신
                if c < candidate_low:
                    candidate_low = c

                # 진입: 저점 대비 entry_pct% 이상 상승
                if c >= candidate_low * entry_mult:
                    signals.iloc[i] = TradingSignal.BUY
                    in_position   = True
                    entry_price   = c
                    highest_price = high[i]
            else:
                if high[i] > highest_price:
                    highest_price = high[i]

                # 익절: 진입가 대비 profit_pct% 상승
                if high[i] >= entry_price * profit_mult:
                    signals.iloc[i] = TradingSignal.SELL
                    in_position   = False
                    candidate_low = c
                # 손절: 진입가 대비 stop_pct% 하락
                elif low[i] <= entry_price * stop_mult:
                    signals.iloc[i] = TradingSignal.SELL
                    in_position   = False
                    candidate_low = c
                # 트레일링 스탑 (trail_pct > 0 일 때만)
                elif self.trail_pct > 0 and low[i] <= highest_price * trail_mult:
                    signals.iloc[i] = TradingSignal.SELL
                    in_position   = False
                    candidate_low = c

        return signals

    def get_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        result = df.copy()
        # 포지션 없을 때 추적 중인 저점과 진입 트리거 기준선
        close = df["close"].values.astype(float)
        n = len(close)
        cand_low_arr   = [None] * n
        entry_lvl_arr  = [None] * n

        in_position   = False
        candidate_low = close[0]
        entry_mult    = 1 + self.entry_pct / 100
        stop_mult     = 1 - self.stop_pct  / 100
        profit_mult   = 1 + self.profit_pct / 100
        entry_price   = 0.0

        for i in range(n):
            c = close[i]
            if not in_position:
                if i > 0 and c < candidate_low:
                    candidate_low = c
                cand_low_arr[i]  = candidate_low
                entry_lvl_arr[i] = candidate_low * entry_mult
                if i > 0 and c >= candidate_low * entry_mult:
                    in_position = True
                    entry_price = c
            else:
                if c >= entry_price * profit_mult or c <= entry_price * stop_mult:
                    in_position   = False
                    candidate_low = c

        result["candidate_low"] = cand_low_arr
        result["entry_level"]   = entry_lvl_arr
        return result

    def validate_params(self, params: dict) -> bool:
        try:
            entry_pct   = float(params.get("entry_pct",   self.entry_pct))
            profit_pct  = float(params.get("profit_pct",  self.profit_pct))
            stop_pct    = float(params.get("stop_pct",    self.stop_pct))
            trail_pct   = float(params.get("trail_pct",   self.trail_pct))
            unit_amount = float(params.get("unit_amount", self.unit_amount))
            return (
                0 < entry_pct  < 100
                and 0 < profit_pct < 100
                and 0 < stop_pct   < 100
                and 0 <= trail_pct < 100
                and unit_amount > 0
            )
        except (TypeError, ValueError):
            return False

    def __repr__(self) -> str:
        trail = f", trail={self.trail_pct}%" if self.trail_pct > 0 else ""
        return (
            f"TrendFollowStrategy("
            f"entry={self.entry_pct}%, profit={self.profit_pct}%, "
            f"stop={self.stop_pct}%{trail}, unit={self.unit_amount:,.0f}원)"
        )
