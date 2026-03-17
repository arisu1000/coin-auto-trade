# 업비트 기반 다층적 AI 에이전트 암호화폐 자동매매 시스템

> **⚠️ 경고**: 이 시스템은 실제 자본으로 매매합니다. 반드시 `TRADING_MODE=paper`(모의매매)로 충분히 검증한 후 실거래를 시작하세요. 개발자는 금융 손실에 책임지지 않습니다.

---

## 시스템 특징

| 기능 | 설명 |
|------|------|
| **다중 AI 에이전트** | LangGraph 기반 Bull/Bear/Judge 토론형 의사결정 |
| **제로 다운타임** | importlib 핫 리로딩으로 서버 재시작 없이 전략 교체 |
| **이중 킬 스위치** | 매크로(포트폴리오)/마이크로(개별 코인) 자동 손절 |
| **ARM64 네이티브** | Apple Silicon Mac에서 TA-Lib 소스 컴파일, 에뮬레이션 없음 |
| **텔레그램 원격 제어** | `/halt`, `/panic_sell`, `/strategy` 등 실시간 제어 |
| **SQLite 영속성** | ACID 보장, 재부팅 후에도 에이전트 맥락 복원 |
| **완전한 테스트 커버리지** | Mock 기반 테스트, 실제 API 호출 없음 |

---

## 빠른 시작

```bash
# 1. 환경 설정
cp .env.example .env
# .env 파일에 API 키 입력

# 2. Docker 빌드 및 실행
docker compose -f docker/docker-compose.yml up

# 3. 텔레그램에서 /start 전송하여 연결 확인
```

자세한 내용은 [설치 가이드](docs/setup.md)를 참조하세요.

---

## 프로젝트 구조

```
coin-auto-trade/
├── docker/               # Docker 설정 (ARM64 TA-Lib 빌드)
├── src/
│   ├── config/           # 환경 설정 (pydantic-settings)
│   ├── exchange/         # 업비트 API 클라이언트 + WebSocket
│   ├── agents/           # LangGraph AI 에이전트 워크플로우
│   ├── strategy/         # 매매 전략 + 핫 리로딩 매니저
│   ├── backtest/         # 백테스팅 엔진 (수수료/슬리피지 반영)
│   ├── persistence/      # SQLite 저장소 레이어
│   ├── kill_switch/      # 이중 킬 스위치 코디네이터
│   ├── bot/              # 텔레그램 봇 핸들러
│   └── core/             # 메인 트레이더 오케스트레이터
├── tests/                # pytest 테스트 스위트
├── scripts/              # 백테스트/초기화 CLI
└── docs/                 # 아키텍처/전략 가이드 문서
```

---

## AI 에이전트 의사결정 흐름

```
시장 데이터
    ↓
Bull Agent (상승 근거 분석) ──┐
                              ├→ Judge Agent → BUY/SELL/HOLD
Bear Agent (하락 위험 분석) ──┘
    ↓
Kill Switch 확인 (낙폭/손절 체크)
    ↓
주문 실행 (모의 또는 실거래)
```

---

## 문서

- [**사용 방법 가이드**](docs/usage-guide.md) ← 여기서 시작하세요
- [설치 및 실행 가이드](docs/setup.md)
- [시스템 아키텍처](docs/architecture.md)
- [전략 작성 가이드](docs/strategy-guide.md)
- [텔레그램 명령어](docs/telegram-commands.md)

---

## 기술 스택

- **언어**: Python 3.11 (asyncio)
- **AI 프레임워크**: LangGraph + LangChain + OpenAI GPT-4o-mini
- **기술적 분석**: TA-Lib (ARM64 네이티브 컴파일)
- **데이터베이스**: SQLite (aiosqlite, WAL 모드)
- **텔레그램**: python-telegram-bot v21 (asyncio 네이티브)
- **인프라**: Docker (linux/arm64)
- **테스트**: pytest + pytest-asyncio + respx

---

## 개발 현황

- [x] 업비트 API 클라이언트 (Rate Limiting + Backoff)
- [x] WebSocket 실시간 호가창 스트리밍
- [x] LangGraph 다중 에이전트 워크플로우
- [x] 핫 리로딩 전략 매니저
- [x] 백테스팅 엔진 (수수료/슬리피지 반영)
- [x] 이중 킬 스위치 시스템
- [x] 텔레그램 봇 원격 제어
- [x] SQLite 영속성 레이어
- [x] pytest 테스트 스위트
- [ ] 공포/탐욕 지수 연동 (Fear & Greed API)
- [ ] 다중 코인 포트폴리오 리밸런싱
- [ ] Prometheus + Grafana 메트릭 대시보드
