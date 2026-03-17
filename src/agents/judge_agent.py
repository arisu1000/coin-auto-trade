"""
심판 에이전트 (Judge Agent)

강세장/약세장 에이전트의 분석을 종합하여 최종 매매 결정을 내린다.

결정 로직:
- net_signal = bull_signal - bear_weight * bear_signal
- net_signal > BUY_THRESHOLD  → BUY (투자 비중 결정)
- net_signal < SELL_THRESHOLD → SELL (청산 비중 결정)
- 그 외                       → HOLD
"""
import json

import structlog
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from src.agents.state import AgentState

logger = structlog.get_logger(__name__)

# 결정 임계값 (보수적 설정)
BUY_THRESHOLD = 0.3
SELL_THRESHOLD = -0.2
BEAR_WEIGHT = 1.3   # 약세 신호에 30% 가중치 (보수적 운용)

JUDGE_SYSTEM_PROMPT = """당신은 리스크 관리를 최우선으로 하는 최종 결정 에이전트입니다.
강세장 에이전트와 약세장 에이전트의 분석을 종합하여 최종 매매 결정을 내립니다.

결정 원칙:
1. 자본 보존이 수익 추구보다 항상 우선합니다
2. 불확실한 상황에서는 HOLD를 선택합니다
3. BUY 결정 시 전체 자본의 최대 30%를 초과하지 않습니다
4. 두 에이전트가 상충하는 경우 약세 에이전트에 더 높은 가중치를 부여합니다

반드시 다음 JSON 형식으로만 응답하세요:
{
  "decision": "BUY" | "SELL" | "HOLD",
  "confidence": <0.0~1.0, 결정 확신도>,
  "position_size_pct": <0~30, 투자 비중 % (BUY/SELL 시에만 의미)>,
  "reasoning": "<100자 이내 결정 근거>"
}"""


async def judge_node(state: AgentState, llm: ChatOpenAI | None = None) -> dict:
    """
    최종 결정 노드

    킬 스위치가 활성화된 경우 즉시 HOLD를 반환한다.
    """
    # 킬 스위치 활성화 시 모든 매매 차단
    if state.get("kill_switch_active", False):
        reason = state.get("kill_switch_reason", "킬 스위치 활성화")
        logger.warning("judge_kill_switch_active", reason=reason)
        return {
            "judge_decision": "HOLD",
            "judge_confidence": 1.0,
            "judge_reasoning": f"킬 스위치 활성화로 매매 차단: {reason}",
            "position_size_pct": 0.0,
        }

    if llm is None:
        from src.config.settings import get_settings
        settings = get_settings()
        llm = ChatOpenAI(
            model=settings.llm_model,
            temperature=settings.llm_temperature,
            api_key=settings.openai_api_key.get_secret_value(),
        )

    bull_signal = state.get("bull_signal", 0.0)
    bear_signal = state.get("bear_signal", 0.0)
    net_signal = bull_signal - BEAR_WEIGHT * bear_signal
    preliminary_action = (
        "BUY" if net_signal > BUY_THRESHOLD
        else "SELL" if net_signal < SELL_THRESHOLD
        else "HOLD"
    )

    market = state["market_data"]
    user_prompt = f"""
마켓: {market['market']} / 현재가: {market['current_price']:,.0f}원

── 에이전트 분석 결과 ──
강세장 에이전트 (signal={bull_signal:.3f}): {state.get('bull_reasoning', '없음')}
약세장 에이전트 (signal={bear_signal:.3f}): {state.get('bear_reasoning', '없음')}

── 수학적 전처리 결과 ──
Net Signal: {net_signal:.3f} (예비 결정: {preliminary_action})
- 계산식: {bull_signal:.3f} - {BEAR_WEIGHT} × {bear_signal:.3f} = {net_signal:.3f}

── 포트폴리오 현황 ──
가용 원화: {state.get('portfolio_krw', 0):,.0f}원
보유 코인: {state.get('portfolio_coin', 0):.6f}
평균 단가: {state.get('portfolio_avg_price', 0):,.0f}원
미실현 손익: {state.get('unrealized_pnl_pct', 0):.2f}%

최종 결정을 내리세요."""

    messages = [SystemMessage(content=JUDGE_SYSTEM_PROMPT), HumanMessage(content=user_prompt)]

    try:
        response = await llm.ainvoke(messages)
        result = json.loads(response.content)

        decision = result.get("decision", "HOLD").upper()
        if decision not in ("BUY", "SELL", "HOLD"):
            decision = "HOLD"

        confidence = float(result.get("confidence", 0.5))
        position_size = float(result.get("position_size_pct", 0.0))
        reasoning = result.get("reasoning", "")

        logger.info(
            "judge_decision",
            decision=decision,
            confidence=confidence,
            position_size_pct=position_size,
            net_signal=net_signal,
        )
        return {
            "judge_decision": decision,
            "judge_confidence": max(0.0, min(1.0, confidence)),
            "judge_reasoning": reasoning,
            "position_size_pct": max(0.0, min(30.0, position_size)),
            "messages": [response],
        }
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        logger.error("judge_agent_parse_error", error=str(e))
        return {
            "judge_decision": "HOLD",
            "judge_confidence": 0.0,
            "judge_reasoning": f"파싱 오류로 HOLD (안전 기본값): {str(e)}",
            "position_size_pct": 0.0,
        }
