"""
약세장 에이전트 (Bear Agent)

시장 데이터에서 하락 위험 신호만을 집중적으로 탐색한다.
강세장 에이전트의 낙관론을 비관적 시각으로 반박하도록 설계됨.
"""
import json

import structlog
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from src.agents.state import AgentState

logger = structlog.get_logger(__name__)

BEAR_SYSTEM_PROMPT = """당신은 약세장 리스크 분석 전문가입니다. 주어진 시장 데이터에서 오직 하락 위험 근거만을 찾아 분석하세요.

분석 시 집중할 요소:
1. RSI가 과매수 구간(70 이상)에서 하락 반전 중인지
2. MACD 데드크로스 또는 음전환 신호
3. 볼린저 밴드 상단 돌파 후 되밀림 (과열 경고)
4. 거래량 감소 동반 약세 (상승 동력 소진)
5. EMA 역배열 (단기 < 장기) 또는 역배열 전환 중
6. 매도 잔량이 매수 잔량보다 현저히 큰 경우
7. 강한 매도 저항 호가 벽 존재

반드시 다음 JSON 형식으로만 응답하세요:
{
  "signal": <0.0에서 1.0 사이의 부동소수점, 1.0이 가장 강한 하락 위험>,
  "reasoning": "<50자 이내의 핵심 위험 근거>",
  "risk_factors": ["<주요 위험 요인 목록>"]
}"""


async def bear_node(state: AgentState, llm: ChatOpenAI | None = None) -> dict:
    """
    약세장 분석 노드

    강세장 에이전트의 분석 결과(bull_signal, bull_reasoning)를
    입력 컨텍스트로 받아, 비관적 시각으로 반박한다.
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
    bull_context = (
        f"강세장 에이전트 의견 (signal={state.get('bull_signal', 0):.2f}): "
        f"{state.get('bull_reasoning', '없음')}"
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

── 상대 의견 (반박 대상) ──
{bull_context}

위 데이터에서 하락 위험을 분석하고 강세장 에이전트의 낙관론을 반박하세요."""

    messages = [SystemMessage(content=BEAR_SYSTEM_PROMPT), HumanMessage(content=user_prompt)]

    try:
        response = await llm.ainvoke(messages)
        result = json.loads(response.content)
        signal = float(result.get("signal", 0.0))
        reasoning = result.get("reasoning", "분석 없음")

        logger.info("bear_agent_result", signal=signal, reasoning=reasoning)
        return {
            "bear_signal": max(0.0, min(1.0, signal)),
            "bear_reasoning": reasoning,
            "messages": [response],
        }
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        logger.error("bear_agent_parse_error", error=str(e))
        return {
            "bear_signal": 0.5,  # 파싱 실패 시 보수적으로 중간값
            "bear_reasoning": f"파싱 오류 (보수적 기본값 적용): {str(e)}",
        }
