"""
터틀 트레이딩 전략 (Turtle Trading System)

1983년 Richard Dennis와 William Eckhardt가 고안한 추세 추종 전략.
"트레이더는 만들어질 수 있다"는 실험에서 탄생했으며,
단순한 규칙 기반임에도 장기적으로 강력한 수익을 기록한 검증된 시스템.

─── 핵심 원리 ─────────────────────────────────────────────────────────
진입: N일 고가(돈치안 채널 상단) 돌파 → 매수
청산: M일 저가(돈치안 채널 하단) 이탈 → 매도  (N > M)

System 1 (단기): 20일 진입 / 10일 청산
System 2 (장기): 55일 진입 / 20일 청산  ← 원조 터틀 시스템

─── ATR 기반 포지션 사이징 ────────────────────────────────────────────
N  = ATR(20)  →  시장 변동성 단위
1유닛 = (계좌잔고 × 위험비율%) / (N × 현재가)
최대 4유닛까지 피라미딩 (0.5N 상승마다 1유닛 추가)

─── 손절 기준 ────────────────────────────────────────────────────────
손절가 = 진입가 - (stop_atr_multiplier × N)
기본값: 진입가 - 2N

─── 시스템 설계 참고 ─────────────────────────────────────────────────
generate_signals()는 돈치안 채널 돌파 시그널을 반환한다.
ATR 계산은 get_indicators()에서 지표 컬럼으로 제공되어
AI 에이전트와 백테스트 엔진이 활용할 수 있도록 한다.
"""

import numpy as np
import pandas as pd
import talib

from src.strategy.base import Strategy, TradingSignal


class TurtleStrategy(Strategy):
    """
    터틀 트레이딩 전략

    파라미터:
        system          : 1 (단기, 20/10) 또는 2 (장기, 55/20)
        entry_period    : 진입 기준 고가 채널 기간 (기본: system에 따라 자동)
        exit_period     : 청산 기준 저가 채널 기간 (기본: system에 따라 자동)
        atr_period      : ATR 계산 기간 (기본: 20)
        stop_atr_mult   : 손절폭 = stop_atr_mult × ATR (기본: 2.0)
        use_filter      : System 1에서 직전 신호가 수익이었으면 건너뜀 (원조 규칙, 기본: False)

    사용 예시:
        TurtleStrategy()               # System 1 기본값
        TurtleStrategy(system=2)       # System 2 (장기 추세 추종)
        TurtleStrategy(entry_period=55, exit_period=20)  # 파라미터 직접 지정
    """

    name = "turtle"

    # 시스템별 기본 파라미터
    _SYSTEM_DEFAULTS = {
        1: {"entry": 20, "exit": 10},
        2: {"entry": 55, "exit": 20},
    }

    def __init__(
        self,
        system: int = 1,
        entry_period: int | None = None,
        exit_period: int | None = None,
        atr_period: int = 20,
        stop_atr_mult: float = 2.0,
        use_filter: bool = False,
    ) -> None:
        if system not in (1, 2):
            raise ValueError("system은 1 또는 2여야 합니다")

        defaults = self._SYSTEM_DEFAULTS[system]
        self.system = system
        self.entry_period = entry_period if entry_period is not None else defaults["entry"]
        self.exit_period = exit_period if exit_period is not None else defaults["exit"]
        self.atr_period = atr_period
        self.stop_atr_mult = stop_atr_mult
        self.use_filter = use_filter

        if self.exit_period >= self.entry_period:
            raise ValueError(
                f"exit_period({self.exit_period})는 entry_period({self.entry_period})보다 작아야 합니다"
            )

    # ── 핵심 시그널 생성 ──────────────────────────────────────────────

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        """
        돈치안 채널 돌파 기반 시그널 생성

        매수 조건: 현재 종가 > 이전 봉 기준 entry_period 최고가
        매도 조건: 현재 종가 < 이전 봉 기준 exit_period 최저가

        '이전 봉 기준'을 사용하는 이유:
            당일 고가/저가가 확정되기 전 데이터를 진입 기준으로 쓰면
            미래 데이터 참조(Look-ahead Bias)가 발생한다.
            shift(1)로 전날까지의 채널을 참조하여 이를 방지한다.
        """
        high = df["high"].values.astype(float)
        low = df["low"].values.astype(float)
        close = df["close"].values.astype(float)
        n = len(df)

        # 돈치안 채널 계산 (shift(1)로 look-ahead bias 방지)
        entry_high = _rolling_max(high, self.entry_period)   # entry_period 최고가
        exit_low   = _rolling_min(low, self.exit_period)     # exit_period 최저가

        # ATR (손절/포지션 사이징용)
        atr = talib.ATR(high, low, close, timeperiod=self.atr_period)

        signals = pd.Series(TradingSignal.HOLD, index=df.index, dtype=int)

        in_position = False
        entry_price = 0.0
        last_trade_profitable = False  # use_filter용

        for i in range(1, n):
            if np.isnan(entry_high[i - 1]) or np.isnan(exit_low[i - 1]):
                continue
            if np.isnan(atr[i]):
                continue

            if not in_position:
                # ── 진입 조건 ─────────────────────────────────────────
                # System 1 필터: 직전 매매가 수익이었으면 이번 신호는 건너뜀
                if self.use_filter and last_trade_profitable:
                    last_trade_profitable = False  # 한 번만 건너뜀
                    continue

                if close[i] > entry_high[i - 1]:
                    signals.iloc[i] = TradingSignal.BUY
                    in_position = True
                    entry_price = close[i]

            else:
                # ── 청산 조건 ─────────────────────────────────────────
                stop_price = entry_price - self.stop_atr_mult * atr[i]
                hit_exit_channel = close[i] < exit_low[i - 1]
                hit_stop_loss    = close[i] < stop_price

                if hit_exit_channel or hit_stop_loss:
                    signals.iloc[i] = TradingSignal.SELL
                    in_position = False
                    last_trade_profitable = close[i] > entry_price
                    entry_price = 0.0

        return signals

    # ── 지표 컬럼 추가 ────────────────────────────────────────────────

    def get_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        백테스트 리포트 및 AI 에이전트 컨텍스트용 지표 컬럼 추가

        추가 컬럼:
            donchian_upper  : entry_period 최고가 (진입 기준선)
            donchian_lower  : entry_period 최저가
            donchian_exit   : exit_period 최저가 (청산 기준선)
            atr             : ATR(atr_period)  — 변동성 단위 N
            stop_price      : 현재 ATR 기준 손절가 (참고용)
            channel_width_pct : 채널 폭 / 현재가 × 100 (변동성 체감 지수)
        """
        result = df.copy()
        high  = df["high"].values.astype(float)
        low   = df["low"].values.astype(float)
        close = df["close"].values.astype(float)

        entry_high = _rolling_max(high, self.entry_period)
        entry_low  = _rolling_min(low, self.entry_period)
        exit_low   = _rolling_min(low, self.exit_period)
        atr        = talib.ATR(high, low, close, timeperiod=self.atr_period)

        result["donchian_upper"]      = entry_high
        result["donchian_lower"]      = entry_low
        result["donchian_exit"]       = exit_low
        result["atr"]                 = atr
        result["stop_price"]          = close - self.stop_atr_mult * atr
        result["channel_width_pct"]   = np.where(
            close > 0,
            (entry_high - entry_low) / close * 100,
            np.nan,
        )
        return result

    # ── 포지션 사이징 헬퍼 ────────────────────────────────────────────

    def calc_unit_size(
        self,
        account_balance: float,
        current_price: float,
        atr: float,
        risk_pct: float = 1.0,
    ) -> float:
        """
        ATR 기반 1유닛 수량 계산 (원조 터틀 공식)

        1유닛 = (계좌잔고 × 위험비율%) / (ATR × 현재가)

        Args:
            account_balance : 가용 자본금 (원)
            current_price   : 현재 코인 가격 (원)
            atr             : 현재 ATR 값
            risk_pct        : 계좌 대비 위험 비율 (%, 기본 1%)

        Returns:
            매수할 코인 수량 (소수점 이하 8자리까지)

        예시:
            계좌 1,000,000원, BTC 50,000,000원, ATR 500,000원, 위험 1%
            → 1유닛 = (1,000,000 × 0.01) / (500,000 × 50,000,000)
            ※ 실제로는 원화 단위 재조정 필요 (아래 구현 참고)
        """
        if atr <= 0 or current_price <= 0:
            return 0.0

        # 터틀 원조 공식:
        #   일일 변동성(KRW) = ATR (이미 KRW 단위)
        #   보유 수량 X에서 하루 리스크 = X * ATR
        #   허용 리스크 = account_balance * risk_pct%
        #   → X = (account_balance * risk_pct%) / ATR
        risk_amount = account_balance * (risk_pct / 100)
        unit = risk_amount / atr   # 코인 수량 (단위: BTC, ETH 등)
        return round(unit, 8)

    # ── 파라미터 검증 ────────────────────────────────────────────────

    def validate_params(self, params: dict) -> bool:
        entry = params.get("entry_period", self.entry_period)
        exit_ = params.get("exit_period", self.exit_period)
        atr_p = params.get("atr_period", self.atr_period)
        stop  = params.get("stop_atr_mult", self.stop_atr_mult)

        if not (isinstance(entry, int) and entry >= 10):
            return False
        if not (isinstance(exit_, int) and exit_ >= 5):
            return False
        if exit_ >= entry:
            return False
        if not (isinstance(atr_p, int) and atr_p >= 2):
            return False
        if stop <= 0:
            return False
        return True

    def __repr__(self) -> str:
        return (
            f"TurtleStrategy(system={self.system}, "
            f"entry={self.entry_period}, exit={self.exit_period}, "
            f"atr={self.atr_period}, stop={self.stop_atr_mult}N)"
        )


# ── 내부 유틸리티 ─────────────────────────────────────────────────────

def _rolling_max(arr: np.ndarray, window: int) -> np.ndarray:
    """
    Look-ahead bias 없는 롤링 최댓값 (shift(1) 적용)

    i번째 값 = arr[i-window : i] 의 최댓값
    즉, '현재 봉 제외' 직전 window개 데이터만 참조한다.
    """
    n = len(arr)
    result = np.full(n, np.nan)
    for i in range(window, n):
        result[i] = np.max(arr[i - window: i])
    return result


def _rolling_min(arr: np.ndarray, window: int) -> np.ndarray:
    """Look-ahead bias 없는 롤링 최솟값 (shift(1) 적용)"""
    n = len(arr)
    result = np.full(n, np.nan)
    for i in range(window, n):
        result[i] = np.min(arr[i - window: i])
    return result
