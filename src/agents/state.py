"""
LangGraph 에이전트 상태 스키마

TypedDict 기반으로 정의하여 LangGraph의 상태 리듀서 프로토콜과 완벽 호환.
모든 노드는 이 상태를 읽고 부분 업데이트(dict)를 반환한다.
"""
from typing import Annotated, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class MarketSnapshot(TypedDict):
    """API에서 수집한 시장 스냅샷"""
    market: str
    current_price: float
    change_rate_24h: float        # 24시간 변동률 (%)
    volume_24h: float
    # 기술적 지표 (TA-Lib 계산 후 채워짐)
    rsi_14: float
    macd: float
    macd_signal: float
    bb_upper: float
    bb_lower: float
    bb_mid: float
    ema_20: float
    ema_50: float
    # 호가창 데이터
    bid_ask_ratio: float          # 매수/매도 잔량 비율 (>1 이면 매수세)
    best_bid: float
    best_ask: float
    total_bid_size: float
    total_ask_size: float
    # 타임스탬프
    timestamp: str


class AgentState(TypedDict):
    """
    LangGraph 그래프 전역 상태

    모든 에이전트 노드가 이 상태를 공유하며, 각 노드는
    변경된 필드만을 담은 dict를 반환한다.
    """
    # 시장 데이터 (Data Gathering 노드에서 채움)
    market_data: MarketSnapshot

    # 포트폴리오 상태 (Trading Loop에서 채움)
    portfolio_krw: float          # 가용 원화
    portfolio_coin: float         # 보유 코인 수량
    portfolio_avg_price: float    # 평균 매수 단가
    unrealized_pnl_pct: float     # 미실현 손익률 (%)

    # 강세장 에이전트 출력
    bull_signal: float            # -1.0 ~ 1.0
    bull_reasoning: str

    # 약세장 에이전트 출력
    bear_signal: float            # -1.0 ~ 1.0 (양수 = 더 약세)
    bear_reasoning: str

    # 심판 에이전트 최종 결정
    judge_decision: str           # "BUY" | "SELL" | "HOLD"
    judge_confidence: float       # 0.0 ~ 1.0
    judge_reasoning: str
    position_size_pct: float      # 투자 비중 (0 ~ 100%)

    # 킬 스위치 상태 (Coordinator에서 관리)
    kill_switch_active: bool
    kill_switch_reason: str

    # 메시지 이력 (LangGraph 누적 리듀서)
    messages: Annotated[list[BaseMessage], add_messages]
