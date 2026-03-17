"""
강세장 에이전트 (Bull Agent)

시장 데이터에서 상승 근거만을 집중적으로 탐색한다.
확증 편향을 의도적으로 부여하여 약세장 에이전트와의 토론에서
균형 잡힌 분석이 가능하도록 설계됨.
"""
import json

import structlog
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from src.agents.state import AgentState

logger = structlog.get_logger(__name__)

BULL_SYSTEM_PROMPT = """당신은 강세장 분석 전문가입니다. 주어진 시장 데이터에서 오직 상승 근거만을 찾아 분석하세요.

분석 시 집중할 요소:
1. RSI가 과매도 구간(30 이하)에서 회복 중인지
2. MACD 골든크로스 또는 양전환 신호
3. 볼린저 밴드 하단에서의 반등 가능성
4. 거래량 급증 동반 여부 (상승 추세 확인)
5. EMA 정배열 (단기 > 장기)
6. 매수 잔량/매도 잔량 비율이 1을 초과하는지

반드시 다음 JSON 형식으로만 응답하세요:
{
  "signal": <-1.0에서 1.0 사이의 부동소수점, 1.0이 가장 강한 매수>,
  "reasoning": "<50자 이내의 핵심 근거>",
  "key_indicators": ["<주요 지표 목록>"]
}"""


async def bull_node(state: AgentState, llm: ChatOpenAI | None = None) -> dict:
    """
    강세장 분석 노드

    Args:
        state: 현재 그래프 상태
        llm: ChatOpenAI 인스턴스 (테스트 시 Mock 주입 가능)

    Returns:
        bull_signal, bull_reasoning 업데이트
    """
    if llm is None:
        from src.config.settings import get_settings
        settings = get_settings()
        llm = ChatOpenAI(
            model=settings.llm_model,
            temperature=settings.llm_temperature,
            api_key=settings.openai_api_key.get_secret_value(),
        )

    market = state["market_data"]
    portfolio_info = (
        f"보유 코인: {state.get('portfolio_coin', 0):.6f}, "
        f"평균 단가: {state.get('portfolio_avg_price', 0):,.0f}원, "
        f"미실현 손익: {state.get('unrealized_pnl_pct', 0):.2f}%"
    )

    user_prompt = f"""
마켓: {market['market']}
현재가: {market['current_price']:,.0f}원
24시간 변동률: {market['change_rate_24h']:.2f}%
거래량: {market['volume_24h']:.4f}

── 기술적 지표 ──
RSI(14): {market['rsi_14']:.2f}
MACD: {market['macd']:.6f} / Signal: {market['macd_signal']:.6f}
볼린저 상단: {market['bb_upper']:,.0f} / 중심: {market['bb_mid']:,.0f} / 하단: {market['bb_lower']:,.0f}
EMA20: {market['ema_20']:,.0f} / EMA50: {market['ema_50']:,.0f}

── 호가창 ──
매수잔량/매도잔량 비율: {market['bid_ask_ratio']:.3f}
최우선 매수가: {market['best_bid']:,.0f} / 최우선 매도가: {market['best_ask']:,.0f}

── 포트폴리오 ──
{portfolio_info}

위 데이터에서 상승 근거를 분석하세요."""

    messages = [SystemMessage(content=BULL_SYSTEM_PROMPT), HumanMessage(content=user_prompt)]

    try:
        response = await llm.ainvoke(messages)
        result = json.loads(response.content)
        signal = float(result.get("signal", 0.0))
        reasoning = result.get("reasoning", "분석 없음")

        logger.info("bull_agent_result", signal=signal, reasoning=reasoning)
        return {
            "bull_signal": max(-1.0, min(1.0, signal)),
            "bull_reasoning": reasoning,
            "messages": [response],
        }
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        logger.error("bull_agent_parse_error", error=str(e))
        return {
            "bull_signal": 0.0,
            "bull_reasoning": f"파싱 오류: {str(e)}",
        }
