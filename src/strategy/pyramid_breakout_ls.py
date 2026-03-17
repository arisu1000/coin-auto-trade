"""
피라미딩 브레이크아웃 롱/숏 전략 (Pyramid Breakout Long-Short)

현물 시장에서는 숏이 불가능하지만, 선물/파생상품 시장을 가정하여
롱/숏 양방향 피라미딩을 시뮬레이션한다.

─── 롱 진입 ─────────────────────────────────────────────────────────────────
  포지션 없을 때 candidate_low를 추적.
  close >= candidate_low × (1 + entry_pct%)  →  BUY

─── 숏 진입 ─────────────────────────────────────────────────────────────────
  포지션 없을 때 candidate_high를 추적.
  close <= candidate_high × (1 - entry_pct%)  →  SHORT

─── 피라미딩 (롱) ───────────────────────────────────────────────────────────
  포지션 보유 중, 직전 매수가 대비 add_pct% 상승마다 BUY

─── 피라미딩 (숏) ───────────────────────────────────────────────────────────
  숏 포지션 보유 중, 직전 숏 진입가 대비 add_pct% 하락마다 SHORT

─── 손절 (롱) ───────────────────────────────────────────────────────────────
  close <= 첫 진입가 × (1 - stop_pct%)  →  SELL

─── 손절 (숏) ───────────────────────────────────────────────────────────────
  close >= 첫 진입가 × (1 + stop_pct%)  →  COVER

─── 익절 — 트레일링 스탑 (롱) ──────────────────────────────────────────────
  close <= 최고가 × (1 - trail_pct%)  →  SELL

─── 익절 — 트레일링 스탑 (숏) ──────────────────────────────────────────────
  close >= 최저가 × (1 + trail_pct%)  →  COVER
"""

import pandas as pd

from src.strategy.base import Strategy, TradingSignal


class PyramidBreakoutLSStrategy(Strategy):
    """
    피라미딩 브레이크아웃 롱/숏 전략

    파라미터:
        entry_pct    : 저점/고점 대비 진입 변동률 (%, 기본 10.0)
        stop_pct     : 첫 진입가 대비 손절 변동률 (%, 기본 10.0)
        trail_pct    : 최고/최저가 대비 익절 변동률 — 트레일링 스탑 (%, 기본 10.0)
        add_pct      : 직전 매수/매도가 대비 피라미딩 변동률 (%, 기본 10.0)
        unit_amount  : 1회 투입 금액 원 (기본 100_000)
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
        for param_name, val in [
            ("entry_pct", entry_pct), ("stop_pct", stop_pct),
            ("trail_pct", trail_pct), ("add_pct", add_pct),
        ]:
            if not (0 < val < 100):
                raise ValueError(f"{param_name}은 0 초과 100 미만이어야 합니다 (현재: {val})")
        if unit_amount <= 0:
            raise ValueError(f"unit_amount는 0보다 커야 합니다 (현재: {unit_amount})")

        self.entry_pct = entry_pct
        self.stop_pct = stop_pct
        self.trail_pct = trail_pct
        self.add_pct = add_pct
        self.unit_amount = unit_amount

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        """
        BUY   : 롱 진입 / 롱 피라미딩
        SELL  : 롱 청산 (손절 또는 트레일링 스탑)
        SHORT : 숏 진입 / 숏 피라미딩
        COVER : 숏 청산 (손절 또는 트레일링 스탑)
        HOLD  : 대기
        """
        close = df["close"].values.astype(float)
        n = len(df)

        signals = pd.Series(TradingSignal.HOLD, index=df.index, dtype=int)

        entry_mult = 1 + self.entry_pct / 100
        stop_mult_long = 1 - self.stop_pct / 100
        trail_mult_long = 1 - self.trail_pct / 100
        stop_mult_short = 1 + self.stop_pct / 100
        trail_mult_short = 1 + self.trail_pct / 100
        add_mult_long = 1 + self.add_pct / 100
        add_mult_short = 1 - self.add_pct / 100

        in_long = False
        in_short = False

        # 롱 상태
        long_entry_price = 0.0
        long_last_add = 0.0
        long_highest = 0.0

        # 숏 상태
        short_entry_price = 0.0
        short_last_add = 0.0
        short_lowest = 0.0

        # 무포지션 시 저점/고점 추적
        candidate_low = close[0]
        candidate_high = close[0]

        for i in range(1, n):
            c = close[i]

            if not in_long and not in_short:
                # ── 저점/고점 갱신 ───────────────────────────────
                if c < candidate_low:
                    candidate_low = c
                if c > candidate_high:
                    candidate_high = c

                # ── 롱 진입 조건 ─────────────────────────────────
                if c >= candidate_low * entry_mult:
                    signals.iloc[i] = TradingSignal.BUY
                    in_long = True
                    long_entry_price = c
                    long_last_add = c
                    long_highest = c
                    # 롱 진입 후 숏 추적 기준점 초기화
                    candidate_high = c

                # ── 숏 진입 조건 (롱 진입 없을 때만) ─────────────
                elif c <= candidate_high * (1 - self.entry_pct / 100):
                    signals.iloc[i] = TradingSignal.SHORT
                    in_short = True
                    short_entry_price = c
                    short_last_add = c
                    short_lowest = c
                    # 숏 진입 후 롱 추적 기준점 초기화
                    candidate_low = c

            elif in_long:
                # ── 롱 최고가 갱신 ───────────────────────────────
                if c > long_highest:
                    long_highest = c

                # ── 롱 손절 ──────────────────────────────────────
                if c <= long_entry_price * stop_mult_long:
                    signals.iloc[i] = TradingSignal.SELL
                    in_long = False
                    candidate_low = c
                    candidate_high = c

                # ── 롱 트레일링 스탑 ─────────────────────────────
                elif c <= long_highest * trail_mult_long:
                    signals.iloc[i] = TradingSignal.SELL
                    in_long = False
                    candidate_low = c
                    candidate_high = c

                # ── 롱 피라미딩 ──────────────────────────────────
                elif c >= long_last_add * add_mult_long:
                    signals.iloc[i] = TradingSignal.BUY
                    long_last_add = c

            elif in_short:
                # ── 숏 최저가 갱신 ───────────────────────────────
                if c < short_lowest:
                    short_lowest = c

                # ── 숏 손절 ──────────────────────────────────────
                if c >= short_entry_price * stop_mult_short:
                    signals.iloc[i] = TradingSignal.COVER
                    in_short = False
                    candidate_low = c
                    candidate_high = c

                # ── 숏 트레일링 스탑 ─────────────────────────────
                elif c >= short_lowest * trail_mult_short:
                    signals.iloc[i] = TradingSignal.COVER
                    in_short = False
                    candidate_low = c
                    candidate_high = c

                # ── 숏 피라미딩 ──────────────────────────────────
                elif c <= short_last_add * add_mult_short:
                    signals.iloc[i] = TradingSignal.SHORT
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
