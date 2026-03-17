# 사용 방법 가이드

> 시스템을 처음 설치하셨다면 [설치 가이드](setup.md)를 먼저 완료하세요.

---

## 목차

1. [처음 시작하기 (3분 안에)](#1-처음-시작하기)
2. [일상적인 운영 흐름](#2-일상적인-운영-흐름)
3. [텔레그램으로 원격 제어하기](#3-텔레그램으로-원격-제어하기)
4. [전략 추가 및 교체하기](#4-전략-추가-및-교체하기)
5. [백테스트로 전략 검증하기](#5-백테스트로-전략-검증하기)
6. [리스크 관리 (킬 스위치)](#6-리스크-관리-킬-스위치)
7. [로그 및 매매 내역 확인](#7-로그-및-매매-내역-확인)
8. [모의매매 → 실거래 전환](#8-모의매매--실거래-전환)
9. [문제 해결 (FAQ)](#9-문제-해결-faq)

---

## 1. 처음 시작하기

### 필수 준비물 체크리스트

```
□ 업비트 계정 + API 키 발급 (읽기 + 주문 권한)
□ OpenAI API 키 (GPT-4o-mini 이상)
□ 텔레그램 봇 토큰 + 나의 Chat ID
□ Docker Desktop 실행 중
```

### API 키 발급 방법

**업비트 API 키**
1. 업비트 로그인 → 마이페이지 → Open API 관리
2. "API 키 발급" 클릭
3. 허용 IP에 내 공인 IP 추가 (보안)
4. 권한 체크: `자산조회`, `주문조회`, `주문하기` (출금은 체크 안 함)

**텔레그램 Chat ID 확인**
```
1. 텔레그램에서 @BotFather 검색
2. /newbot → 봇 이름 입력 → 토큰 복사
3. 내 봇에게 /start 메시지 전송
4. 브라우저에서 아래 URL 열기:
   https://api.telegram.org/bot{토큰}/getUpdates
5. "chat" → "id" 값이 나의 Chat ID
```

### 시작 명령어

```bash
# .env 설정 후
docker compose -f docker/docker-compose.yml up -d

# 정상 시작 확인
docker compose -f docker/docker-compose.yml logs -f trader
```

정상 시작 시 로그:
```json
{"event": "database_connected", "path": "data/db/trading.db"}
{"event": "strategy_loaded", "name": "momentum"}
{"event": "trader_initialized"}
{"event": "trader_started", "mode": "paper"}
{"event": "telegram_bot_started"}
```

텔레그램에서 봇에게 `/start` 전송 → 아래 메시지가 오면 연결 성공:
```
🤖 코인 자동매매 봇

📄 현재 모드: 모의 매매

사용 가능한 명령어:
/status - 현재 상태 조회
...
```

---

## 2. 일상적인 운영 흐름

### 시스템이 자동으로 하는 일

```
매 60초마다 (기본값):
  1. 업비트에서 최근 100개 캔들 수집
  2. TA-Lib로 RSI / MACD / 볼린저 밴드 / EMA 계산
  3. AI 에이전트 3단계 분석:
     Bull Agent  → 상승 근거 점수 산출
     Bear Agent  → 하락 위험 점수 산출
     Judge Agent → 최종 BUY / SELL / HOLD 결정
  4. 킬 스위치 확인 (중단 여부)
  5. BUY/SELL이면 업비트에 주문 발주
  6. SQLite에 결과 기록

매 5분마다:
  - 포트폴리오 스냅샷 저장
  - 낙폭 감시 (매크로 킬 스위치)
```

### 봇이 멈추지 않고 혼자 운영되는 구조

```
트레이딩 루프 ──┐
텔레그램 봇   ──┤ asyncio.gather() → 동시 실행, 서로 블로킹 없음
시장 데이터   ──┤
모니터링      ──┘
```

사용자가 텔레그램 명령을 보내도, 시장 분석 중에도 매매 루프는 멈추지 않습니다.

---

## 3. 텔레그램으로 원격 제어하기

### 현재 상태 확인

```
/status
```

```
📊 시스템 상태

매매 상태: 🟢 운영 중
현재 전략: momentum
총 자산: 1,234,567원
운영 모드: 모의

차단된 마켓: 없음
```

### 로그 확인

```
/logs       ← 최근 10개
/logs 30    ← 최근 30개
```

### 매매 일시 중단

```
/halt
→ ⚠️ 매매를 중단하시겠습니까?
  [✅ 확인 - 매매 중단] [❌ 취소]
```

확인 버튼을 누르면 **신규 주문만 차단**됩니다. 이미 보유 중인 코인은 그대로 유지됩니다.

### 매매 재개

```
/resume
→ ✅ 킬 스위치 해제 완료. 매매를 재개합니다.
```

### 긴급 전량 매도

시장이 폭락하는 것이 눈에 보일 때:

```
/panic_sell
→ 🚨 긴급 전량 매도
  ⚠️ 모든 보유 코인을 즉시 시장가로 매도합니다.
  [🚨 전량 매도 실행] [❌ 취소]
```

> 이 명령은 되돌릴 수 없습니다. 반드시 확인 버튼을 눌러야 실행됩니다.

---

## 4. 전략 추가 및 교체하기

### 현재 사용 가능한 전략

| 전략 이름 | 파일 | 특징 |
|-----------|------|------|
| `momentum` | `src/strategy/momentum.py` | RSI + MACD 추세 추종, 상승장에 강함 |
| `mean_reversion` | `src/strategy/mean_reversion.py` | 볼린저 밴드 평균 회귀, 횡보장에 강함 |
| `turtle` | `src/strategy/turtle.py` | 돈치안 채널 돌파 + ATR 손절, 강한 추세장에 강함 (System 1: 20/10일, System 2: 55/20일) |

### 전략 교체 (서버 재시작 없음)

```
텔레그램에서:
/strategy mean_reversion

→ ✅ 전략이 mean_reversion으로 변경되었습니다.
   서버 재시작 없이 즉시 적용됩니다.
```

### 나만의 전략 만들기

`src/strategy/` 디렉토리에 새 파일 생성:

```python
# src/strategy/my_strategy.py

import pandas as pd
import talib
from src.strategy.base import Strategy, TradingSignal

class MyStrategy(Strategy):
    name = "my_strategy"

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        close = df["close"].values.astype(float)
        rsi = talib.RSI(close, timeperiod=14)

        signals = pd.Series(TradingSignal.HOLD, index=df.index, dtype=int)
        for i in range(len(df)):
            if rsi[i] < 30:
                signals.iloc[i] = TradingSignal.BUY
            elif rsi[i] > 70:
                signals.iloc[i] = TradingSignal.SELL
        return signals

    def validate_params(self, params: dict) -> bool:
        return True
```

파일을 저장하고 텔레그램에서 `/strategy my_strategy` 전송 → 즉시 적용.

> 자세한 전략 작성법은 [전략 작성 가이드](strategy-guide.md) 참조

---

## 5. 백테스트로 전략 검증하기

라이브 투입 전 반드시 과거 데이터로 검증하세요.

### 기본 실행

```bash
# 가상환경 활성화 후
source .venv/bin/activate

# momentum 전략, KRW-BTC, 30일
python scripts/run_backtest.py
```

### 세부 옵션

```bash
python scripts/run_backtest.py \
  --strategy mean_reversion \   # 전략 이름
  --market KRW-ETH \            # 마켓 코드
  --days 90 \                   # 기간 (일)
  --capital 5000000 \           # 초기 자본금 (원)
  --slippage conservative \     # 슬리피지 모델 (fixed / conservative)
  --refresh                     # 캐시 무시하고 데이터 재다운로드
```

### 캔들 데이터 캐시

백테스트를 처음 실행하면 업비트에서 캔들 데이터를 다운로드하여 `data/candles/` 폴더에 저장합니다. 이후 같은 마켓을 다시 실행하면 저장된 데이터를 재사용하고, 마지막 봉 이후분만 추가로 받아옵니다.

```
처음 실행          → 전체 다운로드 → data/candles/KRW_BTC_1m.parquet 저장
이후 실행 (5분 이내) → 다운로드 없이 캐시 즉시 반환
이후 실행 (오래됨)  → 마지막 봉 이후만 증분 다운로드 후 병합
--refresh 옵션     → 캐시 무시, 처음부터 전체 재다운로드
```

```bash
# 평상시 (캐시 자동 활용, 빠름)
python scripts/run_backtest.py --strategy turtle --days 90

# 데이터가 이상하거나 처음부터 다시 받고 싶을 때
python scripts/run_backtest.py --strategy turtle --days 90 --refresh
```

> 캐시 파일은 `data/candles/` 에 마켓별로 저장되며 git에는 포함되지 않습니다.

### 결과 해석

```
📊 백테스트 결과
총 수익률: +12.34%
최대 낙폭(MDD): 8.21%       ← 낮을수록 좋음 (10% 이하 권장)
샤프 지수: 1.234             ← 1.0 이상이면 양호
수익 팩터: 1.87              ← 1.5 이상이면 안정적
승률: 62.3%
총 거래 횟수: 47
최종 자산: 1,123,400원
```

### 투입 기준

| 지표 | 최소 기준 | 권장 |
|------|-----------|------|
| 수익 팩터 | 1.3 이상 | 1.5 이상 |
| MDD | 15% 이하 | 10% 이하 |
| 샤프 지수 | 0.5 이상 | 1.0 이상 |
| 테스트 기간 | 30일 이상 | 90일 이상 |

---

## 6. 리스크 관리 (킬 스위치)

### 자동 킬 스위치 임계값 설정

`.env` 파일에서 조정:

```env
# 포트폴리오 전체가 이 비율 이상 하락하면 모든 매매 자동 중단
MACRO_MAX_DRAWDOWN_PCT=15.0

# 개별 코인이 이 비율 이상 손실 나면 해당 코인 거래 자동 중단
MICRO_STOP_LOSS_PCT=3.0

# 단일 코인 최대 투자 비중
MAX_POSITION_PCT=30.0
```

### 킬 스위치 발동 조건 요약

```
매크로 킬 스위치 (전체 중단)
  ├─ 포트폴리오 낙폭 ≥ 15% → 모든 신규 매매 차단
  └─ 텔레그램 /halt → 관리자 수동 발동

마이크로 킬 스위치 (개별 코인 차단)
  └─ KRW-BTC 손실 ≥ 3% → KRW-BTC만 거래 차단
     (다른 코인 거래는 계속 진행)
```

킬 스위치가 발동되면 텔레그램으로 즉시 알림이 옵니다:
```
🚨 킬 스위치 발동
포트폴리오 낙폭 16.3% - 임계치 15% 초과
```

---

## 7. 로그 및 매매 내역 확인

### 텔레그램에서 확인

```
/logs 20    ← 최근 20개 로그
/status     ← 포트폴리오 현황
```

### 터미널에서 실시간 로그

```bash
docker compose -f docker/docker-compose.yml logs -f trader
```

### SQLite 직접 조회

```bash
# 최근 매매 10건
sqlite3 data/db/trading.db \
  "SELECT market, side, price, volume, pnl, opened_at FROM trades ORDER BY opened_at DESC LIMIT 10;"

# 오늘 수익/손실 합계
sqlite3 data/db/trading.db \
  "SELECT SUM(pnl) as today_pnl, COUNT(*) as trades
   FROM trades
   WHERE status='closed' AND opened_at >= date('now');"

# 자산 변화 (최근 24시간)
sqlite3 data/db/trading.db \
  "SELECT recorded_at, total_krw + coin_value as equity
   FROM portfolio_history
   WHERE recorded_at >= datetime('now', '-24 hours')
   ORDER BY recorded_at;"

# 에러 로그
sqlite3 data/db/trading.db \
  "SELECT created_at, module, message FROM bot_logs
   WHERE level='ERROR' ORDER BY created_at DESC LIMIT 20;"
```

### AI 에이전트 추론 내역 확인

AI가 왜 그 시점에 매수/매도를 결정했는지 복기할 수 있습니다:

```bash
sqlite3 data/db/trading.db \
  "SELECT thread_id, checkpoint FROM agent_checkpoints
   WHERE thread_id LIKE 'main_KRW-BTC%';"
```

---

## 8. 모의매매 → 실거래 전환

### 전환 전 체크리스트

```
□ pytest 전체 통과 확인
  → source .venv/bin/activate && pytest

□ 모의매매로 1주일 이상 운영
  → TRADING_MODE=paper 상태에서 텔레그램 알림, 킬스위치 동작 확인

□ 백테스트 기준 통과 (수익 팩터 1.5+, MDD 10% 이하)

□ 텔레그램 /panic_sell 동작 테스트 (paper 모드에서)

□ 업비트 API 키 권한 확인 (주문하기 권한 포함)
```

### 전환 방법

```env
# .env 파일 수정
TRADING_MODE=live

# 처음에는 소액으로
MAX_POSITION_PCT=5.0   # 전체 자본의 5%만 투자
```

```bash
# 컨테이너 재시작
docker compose -f docker/docker-compose.yml restart trader
```

### 실거래 시작 직후 확인사항

1. 텔레그램 `/status`로 "실거래" 모드 확인
2. 첫 주문 발생 시 업비트 앱에서 실제 체결 확인
3. `sqlite3 data/db/trading.db "SELECT * FROM trades LIMIT 5;"` 로 기록 확인

---

## 9. 문제 해결 (FAQ)

### Q: 봇이 아무 매매도 안 해요

원인과 해결:
```bash
# 1. 킬 스위치 상태 확인
# 텔레그램: /status → "🔴 중단됨" 이면 /resume 실행

# 2. 로그에서 에러 확인
docker compose -f docker/docker-compose.yml logs trader | grep ERROR

# 3. AI가 계속 HOLD를 결정하는 경우 → 정상 (시장 상황에 따라 HOLD가 최선)
# confidence가 0.6 미만이면 주문을 발주하지 않음
```

### Q: 텔레그램 봇이 응답하지 않아요

```bash
# 컨테이너 재시작
docker compose -f docker/docker-compose.yml restart trader

# Chat ID 확인 (.env의 TELEGRAM_CHAT_ID가 정확한지)
curl "https://api.telegram.org/bot{토큰}/getUpdates"
```

### Q: TA-Lib 에러가 나요

```bash
# Docker 이미지 재빌드 (ARM64 소스 컴파일 다시 실행)
docker compose -f docker/docker-compose.yml build --no-cache
```

### Q: 업비트 429 에러가 반복돼요

`.env`에서 API 호출 속도를 낮추세요:

```env
# 기본값 7.0 → 더 낮게 설정
RATE_LIMIT_RPS=5.0
TRADE_INTERVAL_SECONDS=120   # 2분 간격으로 늘리기
```

### Q: 전략을 교체했는데 반영이 안 돼요

```bash
# 파일 수정 시간 확인 (변경이 감지되어야 함)
ls -la src/strategy/

# 또는 텔레그램에서 다시 시도
/strategy 전략이름
```

### Q: DB 파일이 너무 커졌어요

```bash
# 오래된 로그 정리 (30일 이상)
sqlite3 data/db/trading.db \
  "DELETE FROM bot_logs WHERE created_at < datetime('now', '-30 days'); VACUUM;"
```

### Q: 백테스트가 매번 데이터를 새로 받아요 / 캐시를 지우고 싶어요

```bash
# 캐시 파일 목록 확인
ls data/candles/

# 특정 마켓 캐시 삭제 (다음 실행 시 전체 재다운로드)
rm data/candles/KRW_BTC_1m.parquet

# 또는 --refresh 플래그로 캐시 무시
python scripts/run_backtest.py --strategy momentum --days 30 --refresh
```

### Q: Docker 없이 로컬에서 실행하고 싶어요

```bash
source .venv/bin/activate
export $(cat .env | grep -v '^#' | xargs)
python -m src.core.trader
```

---

## 참고 문서

| 문서 | 내용 |
|------|------|
| [설치 가이드](setup.md) | Docker 빌드, 가상환경 설정 |
| [아키텍처](architecture.md) | 시스템 내부 구조 상세 설명 |
| [전략 작성 가이드](strategy-guide.md) | 나만의 전략 코드 작성법 |
| [텔레그램 명령어](telegram-commands.md) | 모든 명령어 상세 레퍼런스 |
