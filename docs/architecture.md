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
    avg_price   = 실제 평균단가 (DB에서 복원 또는 수동 설정)
    candle_high = 해당 캔들의 고가 (intraday 고점 반영)
    candle_low  = 해당 캔들의 저가 (intraday 이탈 감지)
    candle_ts   = 해당 캔들의 시작 시각 (진입 봉 판별)

    # 진입 봉 intraday 배제: 봉 시작 시각이 진입 시각 이전이면(= 진입 전 가격을
    # 고저가에 포함) candle_high/low를 무시하고 현재가만 사용한다.
    bar_after_entry = (entry_ts is None) or (candle_ts >= entry_ts)
    eff_high = candle_high if bar_after_entry else None
    eff_low  = candle_low  if bar_after_entry else None

    highest = max(_position_highest[market], current_price, eff_high)
    _position_highest[market] = highest  ← DB에도 즉시 저장

    stop_threshold  = avg_price × (1 - stop_pct / 100)
    trail_threshold = highest × (1 - trail_pct / 100)
    check_price = eff_low (없으면 current_price)

    check_price <= stop_threshold            → SELL (손절)
    trail_threshold > avg_price
      AND check_price <= trail_threshold     → SELL (트레일링 스탑)
```

**핵심 설계 결정:**
- 손절·트레일링 스탑 기준을 **진입가가 아닌 평균단가(avg_price)** 로 계산합니다. 추가매수로 단가가 낮아진 경우 실제 손익과 일치시키기 위함입니다.
- 트레일링 스탑은 `trail_threshold > avg_price`일 때만 발동합니다. 포지션이 아직 수익권에 진입하지 않은 상태에서 최고가 기준 역방향 스탑이 진입가 근처에서 조기 청산하는 문제를 방지합니다.
- **캔들 고가(HIGH)로 최고가 갱신:** `current_price`(종가)만 보면 intraday 고점을 놓칩니다. 캔들 HIGH를 함께 비교하여 실제 관측 고점을 정확히 추적합니다.
- **캔들 저가(LOW)로 손절·트레일 비교:** 종가 기준이면 캔들 내에서 이미 기준선을 이탈한 경우를 다음 틱까지 감지하지 못합니다. 캔들 LOW를 비교 기준으로 사용해 intraday 이탈을 즉시 감지합니다.
- **진입 봉 intraday 배제 (휩쏘 방지):** 진입 직후의 캔들(특히 진입탐색용 일봉)은 _진입 이전_ 가격까지 고저가에 포함합니다. 그 고가로 최고가를 부풀리고 같은 봉의 진입 전 저가로 트레일/손절을 트리거하면, 매수 1봉 만에 같은 가격으로 즉시 청산되는 휩쏘가 발생합니다(예: 일봉 진입 시 당일 새벽 저가가 트레일을 트리거). 따라서 **봉 시작 시각이 진입 시각 이후인 봉에서만** intraday 고저가를 사용하고, 진입 봉에서는 현재가(종가)만 사용합니다. 진입 시각(`_position_entry_ts`)은 신규 진입·`/pyramid_set`(수동 등록)·`/sync`(잔고 동기화) 시 모두 `_stamp_entry_ts()`로 "현재 시각"을 기록하고 **`pyramid_state.entry_ts` 컬럼에 영속화**되어 재시작 후에도 복원됩니다. 실제 진입 시각을 알 수 없는 동기화·수동 등록도 '지금'으로 **보수적으로** 기록하므로, 등록 직후 현재 진행 중인 봉(시작 시각이 지금보다 앞섬)의 고저가가 배제되어 휩쏘를 막습니다. entry_ts가 없는 이전 버전 레코드만 "오래된 포지션"으로 보고 intraday 값을 그대로 사용합니다. 아울러 매수 즉시 `_held_markets`에 추가해, `monitor_loop`(5분 주기) 갱신 전까지 진입탐색용 캔들 단위가 유지되는 시간을 없앱니다.

포지션 종료(매도·자동정리·동기화 제거) 시에는 `_forget_position()`이 `_pyramid_state`·`_position_highest`·`_position_entry_ts`와 DB 레코드를 일괄 정리하여 상태 누수를 방지합니다.
- **최고가 DB 영속화:** `_position_highest`가 재시작마다 초기화되면 트레일링 스탑 기준선이 낮아집니다. `highest_price` 컬럼을 `pyramid_state` 테이블에 저장하고 최고가 갱신 시마다 즉시 커밋합니다.

### 재시작 시 최고가 복원 순서

```
1. DB load_all() → highest_price > 0 이면 그대로 복원
2. highest_price == 0 (이전 버전 레코드 또는 신규) 이면:
       캔들 히스토리 max(HIGH) 계산
       _position_highest = max(hist_high, entry_price)
       로그: position_highest_restored_from_candles
```

이 절차를 통해 구버전 DB 레코드가 있어도 최고가를 합리적으로 초기화합니다.

---

## 거래대금 급등 감지 (`_detect_surge_markets`)

`TARGET_MARKETS_TOP_N` 모드의 종목 선정 지표인 `acc_trade_price_24h`(24시간 누적
거래대금)는 **후행 지표**입니다. 저거래대금 종목에서 거래량이 폭발해도 24h 누적치가
상위 N위에 진입하기까지 수 시간이 걸려 급등 초기를 놓칩니다.

> 실사례 (2026-06-12, KRW-WAVES): 15:30 KST 거래량 폭발(10분봉 거래대금 2백만 →
> 10억원) 후 19:49에야 top 15 진입. 가격은 이미 393 → 520 고점을 찍고 조정된 뒤라
> 2차 상승의 505원에 진입하게 됨. 이 사례가 본 기능의 도입 배경.

이를 보완하기 위해 `SURGE_DETECT_ENABLED=true`이면 `_monitor_loop`에서 5분마다
전체 KRW 마켓 티커를 조회해 직전 스냅샷 대비 거래대금 증가분(Δ)을 계산합니다
(시간당 1회의 top_n 갱신 틱에서는 티커를 한 번만 조회해 양쪽이 공유):

```
Δ = acc_trade_price_24h(현재) − acc_trade_price_24h(5분 전)
    ↓
급등 판정 (둘 다 충족):
  1. Δ ≥ SURGE_THRESHOLD_KRW (5분 환산 절대 임계값, 기본 3억원)
  2. Δ ≥ 평소 페이스(24h 누적 ÷ 288 구간) × SURGE_MULTIPLIER (기본 10배)
    ↓
감시 목록 + 매수 허용 목록에 즉시 추가 (SURGE_TTL_MINUTES 동안 유지, 기본 4시간)
+ 텔레그램 알림  ※ 동시 감시는 SURGE_MAX_MARKETS개까지 (Δ 큰 순, 기본 5)
```

설계 결정:
- **이중 조건**: 절대 임계값만 쓰면 BTC처럼 평소 거래대금이 큰 종목이 항상 오탐되고,
  배수 조건만 쓰면 먼지 수준 거래대금 종목의 노이즈가 잡힘 → 둘을 AND로 결합
- **top_n 모드 전용** (`_surge_detect_active`): 고정 모드의 TARGET_MARKETS는
  운영자가 노출을 제한하려고 명시한 화이트리스트이므로 급등 감지가 우회하지 않음
- **동시 감시 상한** (`SURGE_MAX_MARKETS`): 장 전체 급등 국면에서 수십 개 상관
  종목에 잔고를 분산 소진하는 것을 방지. 초과분은 `surge_cap_reached`로 로그만 남김
- **비활성화 즉시 반영**: 매수 허용 판정(`_buy_target_markets`)이 매번 플래그를
  확인하므로, `/reload_settings`로 끄면 이미 감지된 종목도 그 즉시 매수 불허
- 빈 티커 응답은 기준 스냅샷을 덮어쓰지 않음 (감지 공백 방지)
- Δ가 롤링 24h 윈도우의 차이라서 24h 전 거래분 이탈만큼 과소평가될 수 있으나,
  급등 감지 목적에는 보수적 오차라 무방
- 급등이 지속되면 TTL만 연장하고 중복 알림은 보내지 않음
- 이미 top N에 있는 종목은 추적하지 않음 (순위 이탈 "제거" 알림과 실제 매수 허용
  상태가 모순되지 않도록); TTL 내에 top N에 진입한 급등 종목은 정규 목록으로 흡수
- 투자유의(`_warned_markets`)·매수 제외(`_excluded_markets`) 마켓은 추가하지 않으며,
  감지 후 지정된 경우에도 다음 틱에 감시에서 즉시 제거(`surge_market_dropped`)

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

## 부분 익절 (`_check_partial_take` / `_execute_partial_sell`)

```
매 전략 루프 틱마다 포지션 보유 시:

    PYRAMID_PARTIAL_TAKE_PCT > 0 이고 partial_taken == False이면:
        target = entry_price × (1 + PYRAMID_PARTIAL_TAKE_PCT / 100)
        current_price >= target
            → _execute_partial_sell(): 보유량 × PYRAMID_PARTIAL_SELL_RATIO 시장가 매도
            → pyramid_state.partial_taken = True (DB 저장)
            → 텔레그램 부분 익절 알림
        나머지 포지션은 트레일링 스탑으로 계속 운영
```

**핵심 설계 결정:**
- `partial_taken` 플래그를 `pyramid_state` 테이블에 영속화하여 재시작 후 이중 발동을 방지합니다.
- 신규 진입(`set_pyramid_state`, `sync_pyramid_state`) 시 플래그를 항상 `False`로 초기화합니다.
- 부분 익절 후에도 `_pyramid_state`는 유지되어 손절·트레일 스탑은 계속 동작합니다.

---

## 일일 성과 리포트 (`_report_loop`)

`asyncio.gather()`에 포함된 별도 코루틴으로 KST 기준 날짜가 바뀔 때 자동 발송합니다.

```
매 60초 체크:
    now_kst.date() != last_report_date
        → _send_daily_report()
        → last_report_date = today_kst

리포트 내용:
    최근 30일 성과 (승률, 누적 손익, MDD)
    현재 자산 (총액, 원화, 코인 평가액)
    보유 포지션별 미실현 손익 (업비트 실시간 조회)
```

최초 기동 시에는 `last_report_date`를 즉시 세팅하되 발송하지 않아 시작 직후 중복 발송을 방지합니다.

---

## 설정값 핫 리로드 (`reload_settings`)

`/reload_settings` 텔레그램 명령으로 실행합니다.

```
Settings() 새 인스턴스 생성 (.env 재파싱, lru_cache 우회)
    ↓
전략 파라미터 변경 시 → StrategyManager.activate() 재실행
킬스위치 임계치 변경 시 → coordinator._macro_threshold / _micro_threshold 갱신
기타 수치 (매매 주기, 캔들, 부분 익절 등) → self._settings 교체
    ↓
핸들러의 self._settings 레퍼런스 동기화 (cmd_reload_settings에서 처리)
```

**반영 안 되는 항목** (재시작 필요): API 키, 텔레그램 토큰, DB 경로, 거래 모드, 로그 레벨

---

## SQLite 테이블 설계

| 테이블 | 목적 | 인덱스 |
|--------|------|--------|
| `trades` | 매매 내역 (입/청산 기록) | market, opened_at |
| `portfolio_history` | 시간별 자산 스냅샷 | recorded_at |
| `bot_logs` | 시스템 로그 (레벨별) | created_at, level |
| `agent_checkpoints` | LangGraph 상태 영속화 | thread_id (PK) |
| `kill_switch_state` | 킬스위치 상태 영속화 (재시작 복원용) | id (PK) |
| `pyramid_state` | 피라미딩 포지션 상태 (진입가·추가매수 횟수·부분익절여부·최고가·진입시각) | market (PK) |
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
