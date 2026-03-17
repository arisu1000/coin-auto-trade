# 시스템 아키텍처 설계서

## 전체 구조

```
┌──────────────────────────────────────────────────────────────────┐
│                    Docker Container (ARM64)                       │
│                                                                  │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │                  Trader (asyncio.gather)                    │  │
│  │                                                            │  │
│  │  ┌─────────────┐  ┌─────────────┐  ┌──────────────────┐  │  │
│  │  │ Market Loop │  │Strategy Loop│  │  Monitor Loop     │  │  │
│  │  │ (캔들수집)   │  │(AI워크플로우│  │ (킬스위치감시)    │  │  │
│  │  │ 60초마다    │  │+주문실행)   │  │  5분마다         │  │  │
│  │  └──────┬──────┘  └──────┬──────┘  └────────┬─────────┘  │  │
│  │         │                │                   │            │  │
│  │         └────────────────┼───────────────────┘            │  │
│  │                          │                                 │  │
│  │  ┌───────────────────────▼──────────────────────────────┐  │  │
│  │  │              공유 상태 (메모리)                        │  │  │
│  │  │  latest_candles | active_orders | kill_switch_status  │  │  │
│  │  └───────────────────────────────────────────────────────┘  │  │
│  │                                                            │  │
│  │  ┌─────────────────────────────────────────────────────┐  │  │
│  │  │                 Telegram Bot                        │  │  │
│  │  │  (asyncio 네이티브, 동일 이벤트 루프)                │  │  │
│  │  └─────────────────────────────────────────────────────┘  │  │
│  └────────────────────────────────────────────────────────────┘  │
│                                                                  │
│  ┌──────────────┐  ┌─────────────────┐  ┌───────────────────┐   │
│  │   SQLite DB  │  │  src/ (볼륨마운트│  │  data/db/ (영속성) │   │
│  │  (WAL 모드)  │  │  핫 리로딩 지원) │  │  호스트 디스크 저장│   │
│  └──────────────┘  └─────────────────┘  └───────────────────┘   │
└──────────────────────────────────────────────────────────────────┘
                              │
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
         업비트 API       OpenAI API      텔레그램 서버
         (REST+WS)        (LLM 추론)       (봇 폴링)
```

---

## LangGraph 에이전트 워크플로우

```
입력: MarketSnapshot (캔들 + 기술적 지표 + 호가창)
  │
  ▼
┌─────────────────┐
│  Bull Agent     │ → 상승 근거 분석 (RSI, MACD, 거래량)
│  bull_signal    │   → 0.7 (강한 매수 근거)
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Bear Agent     │ → 하락 위험 분석 (과열, 저항선, 유동성)
│  bear_signal    │   → 0.2 (약한 하락 위험)
└────────┬────────┘
         │
         ▼
┌─────────────────────────────────────────────┐
│  Judge Agent                                 │
│                                             │
│  net = bull(0.7) - 1.3 × bear(0.2) = 0.44  │
│  0.44 > 0.30 임계치 → BUY 결정              │
│  position_size = 20%                        │
└────────┬────────────────────────────────────┘
         │
         ▼
┌─────────────────┐
│  Kill Switch    │ → is_halted? → HOLD (차단)
│  Coordinator    │ → pass   → 주문 실행
└─────────────────┘
         │
         ▼
   업비트 주문 발주 (paper 모드: 시뮬레이션)
```

---

## 데이터 흐름

```
업비트 REST API
    ↓ get_candles_minutes() [Rate Limited: 9/s]
캔들 데이터 (OHLCV)
    ↓ talib 지표 계산 (RSI, MACD, BB, EMA)
MarketSnapshot 딕셔너리
    ↓ LangGraph.ainvoke()
AgentState (TypedDict)
    ↓ bull_node → bear_node → judge_node
judge_decision: "BUY" | "SELL" | "HOLD"
    ↓ kill_switch.is_market_blocked()?
주문 실행 또는 건너뜀
    ↓ place_order() / cancel_order()
SQLite trades 테이블에 기록
```

---

## 킬 스위치 이중 방어 체계

```
매 전략 루프 틱마다:

┌─────────────────────────────────────────────────────────┐
│                  KillSwitchCoordinator                   │
│                                                         │
│  매크로 킬스위치 (전체 포트폴리오)                        │
│  ├─ 포트폴리오 낙폭 >= 15% → macro_active = True        │
│  └─ 텔레그램 /halt 명령 → manual_halt = True            │
│                                                         │
│  마이크로 킬스위치 (개별 코인)                           │
│  └─ 개별 포지션 손실 >= 3% → blocked_markets.add(coin) │
│                                                         │
│  is_halted = macro_active OR manual_halt               │
│  is_market_blocked(coin) = is_halted OR coin in blocked │
└─────────────────────────────────────────────────────────┘
            │
            ▼
     주문 파이프라인 진입 차단
```

---

## 핫 리로딩 메커니즘

```
파일 변경 감지 (mtime 비교)
    ↓
sys.modules에서 기존 모듈 제거
    ↓
importlib.util.spec_from_file_location()
    ↓
새 모듈 로드 및 Strategy 서브클래스 탐색
    ↓
ABC 검증: generate_signals + validate_params 구현 확인
    ↓                                              │
  통과 → 새 인스턴스 반환                    실패 → TypeError
         다음 틱부터 즉시 적용              라이브 진입 차단
```

---

## SQLite 테이블 설계

| 테이블 | 목적 | 인덱스 |
|--------|------|--------|
| `trades` | 매매 내역 (입/청산 기록) | market, opened_at |
| `portfolio_history` | 시간별 자산 스냅샷 | recorded_at |
| `bot_logs` | 시스템 로그 (레벨별) | created_at, level |
| `agent_checkpoints` | LangGraph 상태 영속화 | thread_id (PK) |

**PRAGMA 설정:**
- `journal_mode=WAL`: 읽기/쓰기 동시 허용
- `synchronous=NORMAL`: 성능-내구성 균형
- `cache_size=-64000`: 64MB 메모리 캐시

---

## Rate Limit 제어 흐름

```
API 요청 시도
    ↓
TokenBucket.acquire() ← 토큰 없으면 asyncio.sleep()
    ↓
HTTP 요청 실행
    ↓
응답 헤더 Remaining-Req 파싱
    │
    ├─ 잔여 < 3: 경고 로그 기록
    ├─ 429 응답: RateLimitError → ExponentialBackoff 재시도
    ├─ 5xx 응답: RetryableError → 재시도
    └─ 4xx 응답: ValueError → 재시도 없음
```
