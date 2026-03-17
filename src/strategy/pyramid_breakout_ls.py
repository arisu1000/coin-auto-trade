"""
피라미딩 브레이크아웃 롱/숏 전략 (Pyramid Breakout Long-Short)

현물 시장에서는 숏이 불가능하지만, 선물/파생상품 시장을 가정하여
롱/숏 양방향 피라미딩을 시뮬레이션한다.

기존 TradingSignal(BUY=1, SELL=-1)을 그대로 사용하고,
숏용 시그널은 정수 -2(SHORT), +2(COVER)로 반환한다.
LSBacktestEngine(engine_ls.py)과 함께 사용해야 한다.

─── 롱 진입 ─────────────────────────────────────────────────────────────────
  무포지션 시 candidate_low 추적.
  close >= candidate_low × (1 + entry_pct%)  →  BUY (1)

─── 숏 진입 ─────────────────────────────────────────────────────────────────
  무포지션 시 candidate_high 추적.
  close <= candidate_high × (1 − entry_pct%)  →  SHORT (−2)

─── 피라미딩 ────────────────────────────────────────────────────────────────
  롱: 직전 매수가 대비 add_pct% 상승마다  →  BUY (1)
  숏: 직전 숏가  대비 add_pct% 하락마다  →  SHORT (−2)

─── 손절 ────────────────────────────────────────────────────────────────────
  롱: close <= 첫 진입가 × (1 − stop_pct%)    →  SELL (−1)
  숏: close >= 첫 진입가 × (1 + stop_pct%)    →  COVER (+2)

─── 트레일링 스탑 ───────────────────────────────────────────────────────────
  롱: close <= 최고가 × (1 − trail_pct%)       →  SELL (−1)
  숏: close >= 최저가 × (1 + trail_pct%)       →  COVER (+2)
"""

import pandas as pd

from src.strategy.base import Strategy, TradingSignal

# 숏용 시그널 (engine_ls.py와 값 일치)
_SHORT = -2
_COVER =  2


class PyramidBreakoutLSStrategy(Strategy):
    """
    피라미딩 브레이크아웃 롱/숏 전략

    파라미터:
        entry_pct    : 저점/고점 대비 진입 변동률 (%, 기본 10.0)
        stop_pct     : 첫 진입가 대비 손절 변동률 (%, 기본 10.0)
        trail_pct    : 최고/최저가 대비 트레일링 스탑 변동률 (%, 기본 10.0)
        add_pct      : 직전 진입가 대비 피라미딩 변동률 (%, 기본 10.0)
        unit_amount  : 1회 투입 금액 원 (기본 100_000)

    LSBacktestEngine과 함께 사용:
        from src.backtest.engine_ls import LSBacktestEngine
        engine = LSBacktestEngine(PyramidBreakoutLSStrategy(), slippage, fee)
        result = engine.run(df)
    """

    name = "pyramid_breakout_ls"

    def __init__(
        self,
        entry_pct: float = 10.0,
        stop_pct: float = 10.0,
        trail_pct: float = 10.0,
        add_pct: float = 10.0,
        unit_amount: float = 100_000.0,
    ) -> None:
        for pname, val in [("entry_pct", entry_pct), ("stop_pct", stop_pct),
                           ("trail_pct", trail_pct), ("add_pct", add_pct)]:
            if not (0 < val < 100):
                raise ValueError(f"{pname}은 0 초과 100 미만이어야 합니다 (현재: {val})")
        if unit_amount <= 0:
            raise ValueError(f"unit_amount는 0보다 커야 합니다 (현재: {unit_amount})")

        self.entry_pct = entry_pct
        self.stop_pct = stop_pct
        self.trail_pct = trail_pct
        self.add_pct = add_pct
        self.unit_amount = unit_amount

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        """
        반환 값:
            BUY   ( 1) : 롱 진입 / 롱 피라미딩
            SELL  (−1) : 롱 청산
            SHORT (−2) : 숏 진입 / 숏 피라미딩
            COVER (+2) : 숏 청산
            HOLD  ( 0) : 대기
        """
        close = df["close"].values.astype(float)
        n = len(df)
        signals = pd.Series(TradingSignal.HOLD, index=df.index, dtype=int)

        entry_mult      = 1 + self.entry_pct  / 100
        stop_long       = 1 - self.stop_pct   / 100
        trail_long      = 1 - self.trail_pct  / 100
        stop_short      = 1 + self.stop_pct   / 100
        trail_short     = 1 + self.trail_pct  / 100
        add_long        = 1 + self.add_pct    / 100
        add_short       = 1 - self.add_pct    / 100

        in_long  = False
        in_short = False

        long_entry_price = long_last_add = long_highest = 0.0
        short_entry_price = short_last_add = short_lowest = 0.0

        candidate_low  = close[0]
        candidate_high = close[0]

        for i in range(1, n):
            c = close[i]

            if not in_long and not in_short:
                if c < candidate_low:
                    candidate_low = c
                if c > candidate_high:
                    candidate_high = c

                if c >= candidate_low * entry_mult:
                    signals.iloc[i] = TradingSignal.BUY
                    in_long = True
                    long_entry_price = long_last_add = long_highest = c
                    candidate_high = c   # 롱 진입 후 숏 추적 기준 리셋

                elif c <= candidate_high * (1 - self.entry_pct / 100):
                    signals.iloc[i] = _SHORT
                    in_short = True
                    short_entry_price = short_last_add = short_lowest = c
                    candidate_low = c    # 숏 진입 후 롱 추적 기준 리셋

            elif in_long:
                if c > long_highest:
                    long_highest = c

                if c <= long_entry_price * stop_long:
                    signals.iloc[i] = TradingSignal.SELL
                    in_long = False
                    candidate_low = candidate_high = c

                elif c <= long_highest * trail_long:
                    signals.iloc[i] = TradingSignal.SELL
                    in_long = False
                    candidate_low = candidate_high = c

                elif c >= long_last_add * add_long:
                    signals.iloc[i] = TradingSignal.BUY
                    long_last_add = c

            else:  # in_short
                if c < short_lowest:
                    short_lowest = c

                if c >= short_entry_price * stop_short:
                    signals.iloc[i] = _COVER
                    in_short = False
                    candidate_low = candidate_high = c

                elif c >= short_lowest * trail_short:
                    signals.iloc[i] = _COVER
                    in_short = False
                    candidate_low = candidate_high = c

                elif c <= short_last_add * add_short:
                    signals.iloc[i] = _SHORT
                    short_last_add = c

        return signals

    def validate_params(self, params: dict) -> bool:
        for key in ("entry_pct", "stop_pct", "trail_pct", "add_pct"):
            val = params.get(key, getattr(self, key))
            if not isinstance(val, (int, float)) or not (0 < val < 100):
                return False
        unit = params.get("unit_amount", self.unit_amount)
        if not isinstance(unit, (int, float)) or unit <= 0:
            return False
        return True

    def __repr__(self) -> str:
        return (
            f"PyramidBreakoutLSStrategy("
            f"entry={self.entry_pct}%, stop={self.stop_pct}%, "
            f"trail={self.trail_pct}%, add={self.add_pct}%, "
            f"unit={self.unit_amount:,.0f}원)"
        )
