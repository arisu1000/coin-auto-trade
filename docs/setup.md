# 설치 및 실행 가이드

## 사전 요구 사항

| 도구 | 버전 | 용도 |
|------|------|------|
| Docker Desktop | 4.x 이상 | ARM64 컨테이너 실행 |
| Python | 3.11+ | 로컬 개발 / 테스트 |
| Git | - | 소스 관리 |

---

## 1단계: 환경 변수 설정

```bash
cp .env.example .env
```

`.env` 파일을 열고 아래 값을 채웁니다:

```env
# 업비트 API 키 (https://upbit.com/mypage/open_api_management)
UPBIT_ACCESS_KEY=your_access_key
UPBIT_SECRET_KEY=your_secret_key

# OpenAI API 키 (https://platform.openai.com/api-keys)
OPENAI_API_KEY=sk-...

# 텔레그램 봇
# 1. @BotFather에서 /newbot 명령으로 생성
# 2. 봇에 /start 메시지 전송 후 아래 URL에서 chat_id 확인:
#    https://api.telegram.org/bot{TOKEN}/getUpdates
TELEGRAM_BOT_TOKEN=123456789:your_token
TELEGRAM_CHAT_ID=your_chat_id

# 처음에는 반드시 paper 모드로 시작!
TRADING_MODE=paper
```

---

## 2단계: Docker 빌드 (첫 실행 시 약 10분 소요)

```bash
# Apple Silicon Mac에서 ARM64 네이티브 빌드
docker compose -f docker/docker-compose.yml build

# 빌드 확인 (TA-Lib import 테스트)
docker compose -f docker/docker-compose.yml run --rm trader python -c "import talib; print('TA-Lib OK:', talib.__version__)"
```

---

## 3단계: 로컬 개발 환경 설정 (테스트 실행용)

```bash
# Python 가상환경 생성
python3.11 -m venv .venv
source .venv/bin/activate

# TA-Lib 로컬 설치 (Mac + Homebrew)
brew install ta-lib
pip install -r requirements-dev.txt

# 테스트 실행 (실제 API 호출 없음, 평균 15초)
pytest
```

---

## 4단계: 첫 실행 (모의매매 모드)

```bash
# 컨테이너 시작
docker compose -f docker/docker-compose.yml up

# 백그라운드 실행
docker compose -f docker/docker-compose.yml up -d

# 로그 모니터링
docker compose -f docker/docker-compose.yml logs -f trader
```

텔레그램에서 봇에 `/start` 메시지를 보내 연결을 확인합니다.

---

## 5단계: 백테스트 실행

```bash
# 기본 (모멘텀 전략, KRW-BTC, 30일)
python scripts/run_backtest.py

# 세부 설정
python scripts/run_backtest.py \
  --strategy mean_reversion \
  --market KRW-ETH \
  --days 90 \
  --capital 5000000 \
  --slippage conservative
```

---

## 실거래 전환 (주의!)

```env
# .env 수정
TRADING_MODE=live
```

반드시 다음을 먼저 확인하세요:
1. `pytest` 전체 통과
2. 모의매매 1주일 이상 안정적 운영
3. 텔레그램 킬스위치(`/halt`) 정상 동작
4. 소액(5,000원)으로 첫 실거래 테스트

---

## 컨테이너 관리

```bash
# 중단
docker compose -f docker/docker-compose.yml down

# 강제 재빌드
docker compose -f docker/docker-compose.yml up --build

# DB 확인
sqlite3 data/db/trading.db "SELECT * FROM trades ORDER BY opened_at DESC LIMIT 10;"
```
