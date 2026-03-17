"""슬리피지 모델 (보수적 체결 시뮬레이션)"""
from abc import ABC, abstractmethod

import pandas as pd


class SlippageModel(ABC):
    @abstractmethod
    def buy_price(self, bar: pd.Series) -> float:
        """매수 체결 가격 (종가보다 불리한 가격)"""
        ...

    @abstractmethod
    def sell_price(self, bar: pd.Series) -> float:
        """매도 체결 가격 (종가보다 불리한 가격)"""
        ...


class FixedBpsSlippage(SlippageModel):
    """
    고정 bps 슬리피지

    매수: close * (1 + bps/10000)
    매도: close * (1 - bps/10000)
    """

    def __init__(self, bps: int = 3) -> None:
        self._factor = bps / 10_000

    def buy_price(self, bar: pd.Series) -> float:
        return float(bar["close"]) * (1 + self._factor)

    def sell_price(self, bar: pd.Series) -> float:
        return float(bar["close"]) * (1 - self._factor)


class ConservativeSlippage(SlippageModel):
    """
    보수적 슬리피지 (최악의 시나리오)

    매수: (close + high) / 2  → 고가 방향 체결 가정
    매도: (close + low) / 2   → 저가 방향 체결 가정
    """

    def buy_price(self, bar: pd.Series) -> float:
        return (float(bar["close"]) + float(bar["high"])) / 2

    def sell_price(self, bar: pd.Series) -> float:
        return (float(bar["close"]) + float(bar["low"])) / 2
