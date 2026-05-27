"""
이동평균 골든크로스 전략 (MA Cross)

── 진입 ─────────────────────────────────────────────────────────────────────
단기 EMA가 장기 EMA를 상향 돌파(골든크로스)할 때 매수.
EMA를 사용해 노이즈를 줄이고 추세 전환 초입에 진입.

── 청산 (먼저 발생하는 조건 적용) ──────────────────────────────────────────
  1. 손절       : 저가 <= 진입가 × (1 - stop_pct%)
  2. 고정 익절  : 고가 >= 진입가 × (1 + profit_pct%)   (profit_pct=0이면 비활성)
  3. 트레일링   : 저가 <= 최고가 × (1 - trail_pct%)    (trail_pct=0이면 비활성)
  4. 데드크로스 : 단기 EMA가 장기 EMA를 하향 돌파       (ma_exit=1이면 활성)

── 파라미터 ─────────────────────────────────────────────────────────────────
  fast_period : 단기 EMA 기간 (기본 5)
  slow_period : 장기 EMA 기간 (기본 20)
  stop_pct    : 손절률 % (기본 3.0)
  profit_pct  : 고정 익절률 % (기본 0 = 비활성)
  trail_pct   : 트레일링 스탑 % (기본 3.0, 0 = 비활성)
  ma_exit     : 데드크로스 시 청산 여부 (1=활성, 0=비활성, 기본 1)
  unit_amount : 1회 투입 금액 원 (기본 100,000)
"""

import pandas as pd

from src.strategy.base import Strategy, TradingSignal


class MACrossStrategy(Strategy):
    """이동평균 골든크로스 전략"""

    name = "ma_cross"

    def __init__(
        self,
        fast_period: int = 5,
        slow_period: int = 20,
        stop_pct: float = 3.0,
        profit_pct: float = 0.0,
        trail_pct: float = 3.0,
        ma_exit: float = 1.0,
        unit_amount: float = 100_000.0,
    ) -> None:
        fast_period = int(fast_period)
        slow_period = int(slow_period)
        if fast_period < 2:
            raise ValueError(f"fast_period는 2 이상이어야 합니다 (현재: {fast_period})")
        if slow_period <= fast_period:
            raise ValueError(
                f"slow_period({slow_period})는 fast_period({fast_period})보다 커야 합니다"
            )
        if not (0 < stop_pct < 100):
            raise ValueError(f"stop_pct은 0 초과 100 미만이어야 합니다 (현재: {stop_pct})")
        if not (0 <= profit_pct < 100):
            raise ValueError(f"profit_pct은 0 이상 100 미만이어야 합니다 (현재: {profit_pct})")
        if not (0 <= trail_pct < 100):
            raise ValueError(f"trail_pct은 0 이상 100 미만이어야 합니다 (현재: {trail_pct})")
        if unit_amount <= 0:
            raise ValueError(f"unit_amount는 0보다 커야 합니다 (현재: {unit_amount})")

        self.fast_period = fast_period
        self.slow_period = slow_period
        self.stop_pct = stop_pct
        self.profit_pct = profit_pct
        self.trail_pct = trail_pct
        self.ma_exit = bool(ma_exit)
        self.unit_amount = unit_amount

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        close = df["close"]
        high  = df["high"].values.astype(float)
        low   = df["low"].values.astype(float)

        fast_ema = close.ewm(span=self.fast_period, adjust=False).mean().values
        slow_ema = close.ewm(span=self.slow_period, adjust=False).mean().values
        n = len(df)

        signals = pd.Series(TradingSignal.HOLD, index=df.index, dtype=int)

        in_position = False
        entry_price = 0.0
        highest_price = 0.0

        stop_mult   = 1 - self.stop_pct   / 100
        profit_mult = 1 + self.profit_pct / 100
        trail_mult  = 1 - self.trail_pct  / 100

        for i in range(1, n):
            if not in_position:
                # 골든크로스: 이전봉 fast ≤ slow → 현재봉 fast > slow
                if fast_ema[i - 1] <= slow_ema[i - 1] and fast_ema[i] > slow_ema[i]:
                    signals.iloc[i] = TradingSignal.BUY
                    in_position = True
                    entry_price = close.iloc[i]
                    highest_price = high[i]
            else:
                if high[i] > highest_price:
                    highest_price = high[i]

                # 손절
                if low[i] <= entry_price * stop_mult:
                    signals.iloc[i] = TradingSignal.SELL
                    in_position = False
                # 고정 익절
                elif self.profit_pct > 0 and high[i] >= entry_price * profit_mult:
                    signals.iloc[i] = TradingSignal.SELL
                    in_position = False
                # 트레일링 스탑
                elif self.trail_pct > 0 and low[i] <= highest_price * trail_mult:
                    signals.iloc[i] = TradingSignal.SELL
                    in_position = False
                # 데드크로스 청산 (ma_exit=True일 때)
                elif (
                    self.ma_exit
                    and fast_ema[i - 1] >= slow_ema[i - 1]
                    and fast_ema[i] < slow_ema[i]
                ):
                    signals.iloc[i] = TradingSignal.SELL
                    in_position = False

        return signals

    def get_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        result = df.copy()
        close = df["close"]
        result[f"ema_{self.fast_period}"] = close.ewm(span=self.fast_period, adjust=False).mean()
        result[f"ema_{self.slow_period}"] = close.ewm(span=self.slow_period, adjust=False).mean()
        return result

    def validate_params(self, params: dict) -> bool:
        try:
            fast        = int(params.get("fast_period", self.fast_period))
            slow        = int(params.get("slow_period", self.slow_period))
            stop_pct    = float(params.get("stop_pct",    self.stop_pct))
            profit_pct  = float(params.get("profit_pct",  self.profit_pct))
            trail_pct   = float(params.get("trail_pct",   self.trail_pct))
            unit_amount = float(params.get("unit_amount", self.unit_amount))
            return (
                fast >= 2
                and slow > fast
                and 0 < stop_pct < 100
                and 0 <= profit_pct < 100
                and 0 <= trail_pct < 100
                and unit_amount > 0
            )
        except (TypeError, ValueError):
            return False

    def __repr__(self) -> str:
        ma_exit_str = "데드크로스청산" if self.ma_exit else "MA청산없음"
        return (
            f"MACrossStrategy("
            f"EMA{self.fast_period}×{self.slow_period}, "
            f"stop={self.stop_pct}%, profit={self.profit_pct}%, "
            f"trail={self.trail_pct}%, {ma_exit_str})"
        )
