"""
모멘텀 전략: RSI + MACD 기반

- RSI < 35: 과매도 구간 → 매수 고려
- RSI > 70: 과매수 구간 → 매도 고려
- MACD 골든크로스(MACD > Signal): 상승 모멘텀 확인
- MACD 데드크로스(MACD < Signal): 하락 모멘텀 확인
"""
import numpy as np
import pandas as pd
import talib

from src.strategy.base import Strategy, TradingSignal


class MomentumStrategy(Strategy):
    name = "momentum"

    def __init__(
        self,
        rsi_period: int = 14,
        rsi_oversold: float = 35.0,
        rsi_overbought: float = 70.0,
        macd_fast: int = 12,
        macd_slow: int = 26,
        macd_signal: int = 9,
    ) -> None:
        self.rsi_period = rsi_period
        self.rsi_oversold = rsi_oversold
        self.rsi_overbought = rsi_overbought
        self.macd_fast = macd_fast
        self.macd_slow = macd_slow
        self.macd_signal_period = macd_signal

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        """RSI + MACD 조합 시그널 생성"""
        close = df["close"].values.astype(float)

        rsi = talib.RSI(close, timeperiod=self.rsi_period)
        macd, macd_sig, _ = talib.MACD(
            close,
            fastperiod=self.macd_fast,
            slowperiod=self.macd_slow,
            signalperiod=self.macd_signal_period,
        )

        signals = pd.Series(TradingSignal.HOLD, index=df.index, dtype=int)

        for i in range(len(df)):
            if np.isnan(rsi[i]) or np.isnan(macd[i]) or np.isnan(macd_sig[i]):
                continue

            rsi_oversold = rsi[i] < self.rsi_oversold
            rsi_overbought = rsi[i] > self.rsi_overbought
            macd_bullish = macd[i] > macd_sig[i]
            macd_bearish = macd[i] < macd_sig[i]

            if rsi_oversold and macd_bullish:
                signals.iloc[i] = TradingSignal.BUY
            elif rsi_overbought and macd_bearish:
                signals.iloc[i] = TradingSignal.SELL

        return signals

    def get_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """지표 컬럼 추가"""
        result = df.copy()
        close = df["close"].values.astype(float)

        result["rsi"] = talib.RSI(close, timeperiod=self.rsi_period)
        macd, signal, hist = talib.MACD(
            close,
            fastperiod=self.macd_fast,
            slowperiod=self.macd_slow,
            signalperiod=self.macd_signal_period,
        )
        result["macd"] = macd
        result["macd_signal"] = signal
        result["macd_hist"] = hist
        return result

    def validate_params(self, params: dict) -> bool:
        rsi_p = params.get("rsi_period", self.rsi_period)
        if not isinstance(rsi_p, int) or rsi_p < 2:
            return False
        oversold = params.get("rsi_oversold", self.rsi_oversold)
        overbought = params.get("rsi_overbought", self.rsi_overbought)
        return oversold < overbought
