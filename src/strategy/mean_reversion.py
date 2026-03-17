"""
평균 회귀 전략: 볼린저 밴드 기반

- 가격이 볼린저 하단 이탈 후 재진입 → 매수
- 가격이 볼린저 상단 돌파 후 되밀림 → 매도
- 횡보장에서 특히 효과적
"""
import numpy as np
import pandas as pd
import talib

from src.strategy.base import Strategy, TradingSignal


class MeanReversionStrategy(Strategy):
    name = "mean_reversion"

    def __init__(
        self,
        bb_period: int = 20,
        bb_std: float = 2.0,
    ) -> None:
        self.bb_period = bb_period
        self.bb_std = bb_std

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        """볼린저 밴드 기반 평균 회귀 시그널"""
        close = df["close"].values.astype(float)

        upper, mid, lower = talib.BBANDS(
            close,
            timeperiod=self.bb_period,
            nbdevup=self.bb_std,
            nbdevdn=self.bb_std,
            matype=0,
        )

        signals = pd.Series(TradingSignal.HOLD, index=df.index, dtype=int)

        for i in range(1, len(df)):
            if any(np.isnan(v) for v in [upper[i], mid[i], lower[i]]):
                continue

            prev_close = close[i - 1]
            curr_close = close[i]

            # 이전 봉이 하단 이탈 → 현재 봉이 하단 위로 복귀 = 매수
            if prev_close < lower[i - 1] and curr_close >= lower[i]:
                signals.iloc[i] = TradingSignal.BUY

            # 이전 봉이 상단 돌파 → 현재 봉이 상단 아래로 되밀림 = 매도
            elif prev_close > upper[i - 1] and curr_close <= upper[i]:
                signals.iloc[i] = TradingSignal.SELL

        return signals

    def get_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        result = df.copy()
        close = df["close"].values.astype(float)
        upper, mid, lower = talib.BBANDS(
            close,
            timeperiod=self.bb_period,
            nbdevup=self.bb_std,
            nbdevdn=self.bb_std,
            matype=0,
        )
        result["bb_upper"] = upper
        result["bb_mid"] = mid
        result["bb_lower"] = lower
        return result

    def validate_params(self, params: dict) -> bool:
        period = params.get("bb_period", self.bb_period)
        std = params.get("bb_std", self.bb_std)
        return isinstance(period, int) and period >= 2 and std > 0
