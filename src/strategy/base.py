"""
매매 전략 추상 기본 클래스 (ABC)

모든 사용자 정의 전략은 Strategy를 상속받아야 한다.
필수 메서드 미구현 시 TypeError 발생 → 결함 있는 전략의 라이브 진입 원천 차단.
"""
from abc import ABC, abstractmethod
from enum import IntEnum

import pandas as pd


class TradingSignal(IntEnum):
    """매매 시그널 표준 열거형"""
    SHORT = -2   # 숏 진입 / 숏 피라미딩 추가
    SELL  = -1   # 롱 청산
    HOLD  =  0
    BUY   =  1   # 롱 진입 / 롱 피라미딩 추가
    COVER =  2   # 숏 청산


class Strategy(ABC):
    """
    매매 전략 인터페이스

    모든 전략은 이 클래스를 상속받고 아래 두 메서드를 반드시 구현해야 한다.

    예시:
        class MyStrategy(Strategy):
            name = "my_strategy"

            def generate_signals(self, df: pd.DataFrame) -> pd.Series:
                # 시그널 계산 로직
                return signals

            def validate_params(self, params: dict) -> bool:
                return True
    """

    #: 전략 식별자 (파일명과 동일하게 설정 권장)
    name: str = "unnamed"

    @abstractmethod
    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        """
        OHLCV 데이터프레임을 받아 각 봉에 대한 매매 시그널을 반환한다.

        Args:
            df: 컬럼 [open, high, low, close, volume] 포함 DataFrame
                인덱스는 datetime

        Returns:
            TradingSignal 값(−1, 0, 1)으로 구성된 pd.Series
            인덱스는 df.index와 동일
        """
        ...

    @abstractmethod
    def validate_params(self, params: dict) -> bool:
        """
        전략 파라미터 유효성 검사

        Args:
            params: 전략별 파라미터 딕셔너리

        Returns:
            True if valid, False otherwise
        """
        ...

    def get_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        (선택 구현) 기술적 지표 컬럼이 추가된 DataFrame 반환

        백테스트 리포트 및 에이전트 컨텍스트 구성에 활용된다.
        기본 구현은 원본 df를 그대로 반환한다.
        """
        return df

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name={self.name!r})"
