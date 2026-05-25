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

## 백테스트 캔들 캐시 흐름

```
run_backtest.py 실행
    ↓
candle_cache.get_missing_range()
    │
    ├─ 캐시 없음 ──────────────────→ 업비트 전체 구간 다운로드
    │                                      ↓
    ├─ 캐시 있음 + 최신 (5분 이내) → 다운로드 없이 즉시 반환
    │
    └─ 캐시 있음 + 오래됨 ─────────→ 마지막 봉 이후만 증분 다운로드
                                           ↓
                              기존 캐시 + 새 데이터 병합 (중복 제거)
                                           ↓
                              data/candles/{MARKET}_{UNIT}m.parquet 저장
                                           ↓
                              요청 기간만큼 슬라이싱 후 반환
```

캐시 파일은 `data/candles/` 에 마켓·봉 단위별로 저장되며, `.gitignore`에 포함되어 저장소에는 커밋되지 않습니다.

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

킬스위치 상태는 SQLite에 영속화되어 **재시작 후에도 복원**됩니다.

### 킬스위치 해제

- `/resume` → 매크로·수동 킬스위치 전체 해제 (`reset(confirm=True)`)
- `/resume KRW-BTC` → 해당 마켓의 마이크로 킬스위치만 해제 (`reset_market(market)`)

---

## 피라미딩 포지션 직접 손절 체크 (`_check_position_exit`)

`generate_signals()`는 캔들 윈도우를 기반으로 시뮬레이션하므로, 윈도우 이전에 진입한 포지션(예: `/sync`·`/pyramid_set`으로 수동 등록)의 손절·트레일링 스탑을 감지하지 못합니다.

이를 보완하기 위해 `_check_position_exit()`가 별도로 동작합니다:

```
포지션이 _pyramid_state에 있으면:
    avg_price = 실제 평균단가 (DB에서 복원 또는 수동 설정)
    highest   = 해당 마켓의 포지션 보유 이후 최고가

    stop_threshold  = avg_price × (1 - stop_pct / 100)
    trail_threshold = highest × (1 - trail_pct / 100)

    current_price <= stop_threshold            → SELL (손절)
    trail_threshold > avg_price
      AND current_price <= trail_threshold     → SELL (트레일링 스탑)
```

**핵심 설계 결정:**
- 손절·트레일링 스탑 기준을 **진입가가 아닌 평균단가(avg_price)** 로 계산합니다. 추가매수로 단가가 낮아진 경우 실제 손익과 일치시키기 위함입니다.
- 트레일링 스탑은 `trail_threshold > avg_price`일 때만 발동합니다. 포지션이 아직 수익권에 진입하지 않은 상태에서 최고가 기준 역방향 스탑이 진입가 근처에서 조기 청산하는 문제를 방지합니다.

---

## 거래지원 종료 예정 마켓 감지

`_monitor_loop` 내 `_refresh_warned_markets()`가 5분마다 실행됩니다:

```
업비트 /market/all?isDetails=true 조회
    ↓
market_event.warning == true  OR  market_warning != "NONE"
인 KRW 마켓 수집 → new_warned set
    ↓
newly_warned = new_warned - _warned_markets (이전 상태와 비교)
    ↓
신규 감지된 마켓에 대해:
  ├─ 보유 중 → 시장가 자동 매도 + 매수 금지 등록 + 텔레그램 알림
  └─ 미보유  → 신규 매수 차단 + 텔레그램 알림
    ↓
_warned_markets = new_warned (업데이트)
```

재시작 시 `_warned_markets`를 조용히 선로딩하여 **이미 알린 마켓을 다시 알림하지 않습니다.**

---

## 매수 금지 마켓 (`_excluded_markets`)

두 가지 경로로 설정됩니다:

| 경로 | 범위 | 영속성 |
|------|------|--------|
| `.env` `EXCLUDED_MARKETS=KRW-SHIB,...` | 시작부터 정적 제외 | 재시작마다 적용 |
| 텔레그램 `/block [마켓]` | 런타임 동적 추가 | SQLite `excluded_markets` 테이블에 저장 |

`_strategy_loop`에서 BUY 결정 직전에 `_excluded_markets` 포함 여부와 `_warned_markets` 포함 여부를 모두 확인합니다.

---

## 매도 후 재진입 쿨다운 (`sell_cooldown`)

매도 체결 시 SQLite `sell_cooldown` 테이블에 매도 시각을 기록합니다.
이후 `PYRAMID_SELL_COOLDOWN_MINUTES`(기본 1440분) 이내에 동일 마켓의 신규 BUY 신호가 오면 진입을 건너뜁니다.
새로운 매수가 체결되면 쿨다운 레코드를 삭제합니다.

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
| `kill_switch_state` | 킬스위치 상태 영속화 (재시작 복원용) | id (PK) |
| `pyramid_state` | 피라미딩 포지션 상태 (진입가·추가매수 횟수) | market (PK) |
| `sell_cooldown` | 매도 후 재진입 대기 기록 | market (PK) |
| `excluded_markets` | 매수 금지 마켓 목록 (사유 포함) | market (PK) |

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
