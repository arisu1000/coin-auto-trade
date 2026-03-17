# 매매 전략 작성 가이드

## 전략의 구조

모든 전략은 `Strategy` 추상 기본 클래스를 상속받아야 합니다.
미구현 시 `TypeError`가 발생하여 라이브 진입이 차단됩니다.

```python
# src/strategy/my_strategy.py

import pandas as pd
import talib
from src.strategy.base import Strategy, TradingSignal


class MyStrategy(Strategy):
    name = "my_strategy"  # 파일명과 동일하게 설정 권장

    def __init__(self, param1: int = 14):
        self.param1 = param1

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        """
        핵심 메서드: OHLCV 데이터 → 매매 시그널

        Returns:
            pd.Series with values:
              TradingSignal.BUY  (=  1): 매수
              TradingSignal.HOLD (=  0): 관망
              TradingSignal.SELL (= -1): 매도
        """
        close = df["close"].values.astype(float)
        rsi = talib.RSI(close, timeperiod=self.param1)

        signals = pd.Series(TradingSignal.HOLD, index=df.index, dtype=int)
        for i in range(len(df)):
            if rsi[i] < 30:
                signals.iloc[i] = TradingSignal.BUY
            elif rsi[i] > 70:
                signals.iloc[i] = TradingSignal.SELL
        return signals

    def validate_params(self, params: dict) -> bool:
        """파라미터 유효성 검사"""
        period = params.get("param1", self.param1)
        return isinstance(period, int) and period >= 2

    def get_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """(선택) 지표 컬럼 추가 - 백테스트 리포트용"""
        result = df.copy()
        result["rsi"] = talib.RSI(df["close"].values.astype(float), timeperiod=self.param1)
        return result
```

---

## 전략 파일 배치

```
src/strategy/
├── base.py            # 수정 금지 (기본 클래스)
├── manager.py         # 수정 금지 (핫 리로드 엔진)
├── momentum.py        # 내장 전략: RSI + MACD 추세 추종
├── mean_reversion.py  # 내장 전략: 볼린저 밴드 평균 회귀
├── turtle.py          # 내장 전략: 터틀 트레이딩 (돈치안 채널)
└── my_strategy.py     # 내가 만든 전략 (여기에 저장)
```

---

## 핫 리로딩으로 전략 교체

서버를 **재시작하지 않고** 전략을 교체할 수 있습니다:

```bash
# 방법 1: 텔레그램 명령어
/strategy my_strategy

# 방법 2: Docker 볼륨 마운트 이용
# src/strategy/my_strategy.py 파일 수정 저장
# → 다음 틱에 자동으로 새 버전 로드됨
```

---

## 전략 검증 순서

새 전략을 라이브에 투입하기 전 반드시 다음 단계를 거쳐야 합니다:

```bash
# 1단계: 단위 테스트
pytest tests/unit/test_strategy_manager.py -v

# 2단계: 백테스트 (30일 이상, 수익 팩터 1.5+ 확인)
python scripts/run_backtest.py --strategy my_strategy --days 90

# 3단계: 모의매매 1주일 운영
# .env의 TRADING_MODE=paper 상태에서 실행

# 4단계: 소액 실거래 테스트
# TRADING_MODE=live, MAX_POSITION_PCT=5.0으로 설정
```

---

## 내장 TA-Lib 함수 예시

```python
import talib

# 이동평균
talib.SMA(close, timeperiod=20)      # 단순 이동평균
talib.EMA(close, timeperiod=20)      # 지수 이동평균

# 모멘텀
talib.RSI(close, timeperiod=14)      # RSI
talib.MACD(close, 12, 26, 9)         # MACD (macd, signal, hist 반환)
talib.STOCH(high, low, close)        # 스토캐스틱

# 변동성
talib.BBANDS(close, 20, 2, 2)        # 볼린저 밴드 (upper, mid, lower)
talib.ATR(high, low, close, 14)      # ATR

# 패턴
talib.CDLHAMMER(open, high, low, close)  # 해머 캔들 패턴
talib.CDLENGULFING(open, high, low, close)  # 장악형 패턴
```

---

## 주의 사항

- `generate_signals`는 **매 틱마다 전체 DataFrame**을 받습니다
- 실시간 진입 시그널은 **마지막 행(`df.iloc[-1]`)**만 사용됩니다
- 초기화 기간(warm-up)이 필요한 지표는 `NaN`이 포함됩니다 → `isnan` 체크 필수
- 전략 내에서 직접 API 호출이나 주문을 하지 마세요 — 실행은 `Trader`가 담당합니다
