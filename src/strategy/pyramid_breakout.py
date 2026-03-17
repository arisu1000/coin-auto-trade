"""
피라미딩 브레이크아웃 전략 (Pyramid Breakout)

─── 진입 ────────────────────────────────────────────────────────────────────
포지션이 없을 때 직전 저점(candidate_low)을 추적한다.
  close >= candidate_low × (1 + entry_pct%)  →  첫 매수 (BUY)

─── 추가 매수 (피라미딩) ────────────────────────────────────────────────────
포지션 보유 중, 직전 매수가 대비 add_pct% 상승마다 동일 금액 추가 매수 (BUY)

─── 손절 ────────────────────────────────────────────────────────────────────
  close <= 첫 진입가 × (1 − stop_pct%)  →  전량 매도 (SELL)

─── 익절 (트레일링 스탑) ────────────────────────────────────────────────────
  close <= 최고가 × (1 − trail_pct%)  →  전량 매도 (SELL)
  (최고가는 매 봉 갱신)

─── 투입 금액 ───────────────────────────────────────────────────────────────
unit_amount: 매수 1회당 투입 금액(원). 첫 진입과 피라미딩 모두 동일.
"""

import numpy as np
import pandas as pd

from src.strategy.base import Strategy, TradingSignal


class PyramidBreakoutStrategy(Strategy):
    """
    피라미딩 브레이크아웃 전략

    파라미터:
        entry_pct    : 직전 저점 대비 진입 상승률 (%, 기본 10.0)
        stop_pct     : 첫 진입가 대비 손절 하락률 (%, 기본 10.0)
        trail_pct    : 최고점 대비 익절 하락률 — 트레일링 스탑 (%, 기본 10.0)
        add_pct      : 직전 매수가 대비 추가 진입 상승률 (%, 기본 10.0)
        unit_amount  : 1회 투입 금액 원 (기본 100_000)

    사용 예시:
        PyramidBreakoutStrategy()
        PyramidBreakoutStrategy(entry_pct=5.0, stop_pct=5.0, unit_amount=500_000)
    """

    name = "pyramid_breakout"

    def __init__(
        self,
        entry_pct: float = 10.0,
        stop_pct: float = 10.0,
        trail_pct: float = 10.0,
        add_pct: float = 10.0,
        unit_amount: float = 100_000.0,
    ) -> None:
        for name, val in [("entry_pct", entry_pct), ("stop_pct", stop_pct),
                          ("trail_pct", trail_pct), ("add_pct", add_pct)]:
            if not (0 < val < 100):
                raise ValueError(f"{name}은 0 초과 100 미만이어야 합니다 (현재: {val})")
        if unit_amount <= 0:
            raise ValueError(f"unit_amount는 0보다 커야 합니다 (현재: {unit_amount})")

        self.entry_pct = entry_pct
        self.stop_pct = stop_pct
        self.trail_pct = trail_pct
        self.add_pct = add_pct
        self.unit_amount = unit_amount

    # ── 핵심 시그널 생성 ──────────────────────────────────────────────────

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        """
        매 봉 종가 기준으로 시그널을 생성한다.

        BUY  : 첫 진입 또는 피라미딩 추가 매수
        SELL : 손절 또는 익절(트레일링 스탑)
        HOLD : 대기

        실시간 트레이더는 마지막 봉(df.iloc[-1])의 시그널만 사용한다.
        """
        close = df["close"].values.astype(float)
        n = len(df)

        signals = pd.Series(TradingSignal.HOLD, index=df.index, dtype=int)

        in_position = False
        entry_price = 0.0       # 첫 진입가 (손절 기준)
        last_add_price = 0.0    # 직전 매수가 (피라미딩 기준)
        highest_price = 0.0     # 진입 이후 최고가 (트레일링 스탑 기준)
        candidate_low = close[0]  # 포지션 없을 때 추적하는 직전 저점

        entry_mult = 1 + self.entry_pct / 100
        stop_mult = 1 - self.stop_pct / 100
        trail_mult = 1 - self.trail_pct / 100
        add_mult = 1 + self.add_pct / 100

        for i in range(1, n):
            c = close[i]

            if not in_position:
                # ── 진입 조건 확인 ────────────────────────────────────────
                if c >= candidate_low * entry_mult:
                    signals.iloc[i] = TradingSignal.BUY
                    in_position = True
                    entry_price = c
                    last_add_price = c
                    highest_price = c
                else:
                    # 진입 전: 저점 계속 갱신
                    if c < candidate_low:
                        candidate_low = c

            else:
                # ── 최고가 갱신 ───────────────────────────────────────────
                if c > highest_price:
                    highest_price = c

                # ── 손절: 첫 진입가 기준 ─────────────────────────────────
                if c <= entry_price * stop_mult:
                    signals.iloc[i] = TradingSignal.SELL
                    in_position = False
                    candidate_low = c  # 청산 후 저점 초기화

                # ── 익절: 최고가 기준 트레일링 스탑 ─────────────────────
                elif c <= highest_price * trail_mult:
                    signals.iloc[i] = TradingSignal.SELL
                    in_position = False
                    candidate_low = c  # 청산 후 저점 초기화

                # ── 피라미딩: 직전 매수가 기준 ──────────────────────────
                elif c >= last_add_price * add_mult:
                    signals.iloc[i] = TradingSignal.BUY
                    last_add_price = c

        return signals

    # ── 지표 컬럼 추가 ────────────────────────────────────────────────────

    def get_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        백테스트 리포트용 참고 지표 추가

        추가 컬럼:
            candidate_low  : 포지션 없을 때 추적 중인 직전 저점
            entry_level    : 진입 트리거 가격 (candidate_low × (1 + entry_pct%))
        """
        result = df.copy()
        close = df["close"].values.astype(float)
        n = len(close)

        cand_low_arr = np.full(n, np.nan)
        entry_level_arr = np.full(n, np.nan)

        in_position = False
        candidate_low = close[0]
        entry_mult = 1 + self.entry_pct / 100
        stop_mult = 1 - self.stop_pct / 100
        trail_mult = 1 - self.trail_pct / 100
        add_mult = 1 + self.add_pct / 100
        entry_price = 0.0
        last_add_price = 0.0
        highest_price = 0.0

        for i in range(n):
            c = close[i]
            if not in_position:
                cand_low_arr[i] = candidate_low
                entry_level_arr[i] = candidate_low * entry_mult
                if i > 0:
                    if c >= candidate_low * entry_mult:
                        in_position = True
                        entry_price = c
                        last_add_price = c
                        highest_price = c
                    elif c < candidate_low:
                        candidate_low = c
            else:
                if c > highest_price:
                    highest_price = c
                if c <= entry_price * stop_mult or c <= highest_price * trail_mult:
                    in_position = False
                    candidate_low = c
                elif c >= last_add_price * add_mult:
                    last_add_price = c

        result["candidate_low"] = cand_low_arr
        result["entry_level"] = entry_level_arr
        return result

    # ── 파라미터 검증 ────────────────────────────────────────────────────

    def validate_params(self, params: dict) -> bool:
        checks = [
            ("entry_pct", self.entry_pct),
            ("stop_pct", self.stop_pct),
            ("trail_pct", self.trail_pct),
            ("add_pct", self.add_pct),
        ]
        for key, default in checks:
            val = params.get(key, default)
            if not isinstance(val, (int, float)) or not (0 < val < 100):
                return False
        unit = params.get("unit_amount", self.unit_amount)
        if not isinstance(unit, (int, float)) or unit <= 0:
            return False
        return True

    def __repr__(self) -> str:
        return (
            f"PyramidBreakoutStrategy("
            f"entry={self.entry_pct}%, stop={self.stop_pct}%, "
            f"trail={self.trail_pct}%, add={self.add_pct}%, "
            f"unit={self.unit_amount:,.0f}원)"
        )
