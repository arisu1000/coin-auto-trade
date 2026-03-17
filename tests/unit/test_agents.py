"""
AI 에이전트 단위 테스트

Mock LLM으로 API 비용 없이 파이프라인 로직 검증
"""
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.bull_agent import bull_node
from src.agents.bear_agent import bear_node
from src.agents.judge_agent import judge_node
from src.agents.state import AgentState


def make_market_snapshot(market: str = "KRW-BTC") -> dict:
    return {
        "market": market,
        "current_price": 50_000_000.0,
        "change_rate_24h": 2.5,
        "volume_24h": 100.0,
        "rsi_14": 45.0,
        "macd": 10000.0,
        "macd_signal": 8000.0,
        "bb_upper": 52_000_000.0,
        "bb_lower": 48_000_000.0,
        "bb_mid": 50_000_000.0,
        "ema_20": 49_500_000.0,
        "ema_50": 48_000_000.0,
        "bid_ask_ratio": 1.2,
        "best_bid": 49_990_000.0,
        "best_ask": 50_010_000.0,
        "total_bid_size": 5.0,
        "total_ask_size": 4.0,
        "timestamp": "2024-01-01T00:00:00Z",
    }


def make_state(**kwargs) -> AgentState:
    defaults = {
        "market_data": make_market_snapshot(),
        "portfolio_krw": 1_000_000.0,
        "portfolio_coin": 0.0,
        "portfolio_avg_price": 0.0,
        "unrealized_pnl_pct": 0.0,
        "bull_signal": 0.0,
        "bull_reasoning": "",
        "bear_signal": 0.0,
        "bear_reasoning": "",
        "judge_decision": "HOLD",
        "judge_confidence": 0.0,
        "judge_reasoning": "",
        "position_size_pct": 0.0,
        "kill_switch_active": False,
        "kill_switch_reason": "",
        "messages": [],
    }
    defaults.update(kwargs)
    return defaults


class TestBullAgent:
    async def test_returns_valid_signal_range(self, mock_llm_bull):
        """신호값이 -1.0 ~ 1.0 범위 내"""
        state = make_state()
        result = await bull_node(state, llm=mock_llm_bull)
        assert -1.0 <= result["bull_signal"] <= 1.0

    async def test_returns_reasoning(self, mock_llm_bull):
        """추론 문자열 반환"""
        state = make_state()
        result = await bull_node(state, llm=mock_llm_bull)
        assert isinstance(result["bull_reasoning"], str)
        assert len(result["bull_reasoning"]) > 0

    async def test_handles_invalid_json(self):
        """JSON 파싱 실패 시 기본값 반환 (시스템 중단 없음)"""
        mock_llm = AsyncMock()
        mock_llm.ainvoke.return_value = MagicMock(content="invalid json{{")
        state = make_state()
        result = await bull_node(state, llm=mock_llm)
        assert result["bull_signal"] == 0.0
        assert "파싱 오류" in result["bull_reasoning"]

    async def test_clamps_signal_out_of_range(self):
        """범위 초과 신호값 클램핑"""
        mock_llm = AsyncMock()
        mock_llm.ainvoke.return_value = MagicMock(
            content='{"signal": 2.5, "reasoning": "extreme"}'
        )
        state = make_state()
        result = await bull_node(state, llm=mock_llm)
        assert result["bull_signal"] == 1.0  # 1.0으로 클램핑


class TestBearAgent:
    async def test_bear_signal_non_negative(self, mock_llm_bear):
        """약세 신호는 0 이상"""
        state = make_state(bull_signal=0.7, bull_reasoning="강세 근거")
        result = await bear_node(state, llm=mock_llm_bear)
        assert result["bear_signal"] >= 0.0

    async def test_returns_reasoning(self, mock_llm_bear):
        state = make_state()
        result = await bear_node(state, llm=mock_llm_bear)
        assert isinstance(result["bear_reasoning"], str)

    async def test_handles_invalid_json(self):
        """파싱 실패 시 보수적 기본값 (0.5)"""
        mock_llm = AsyncMock()
        mock_llm.ainvoke.return_value = MagicMock(content="broken")
        state = make_state()
        result = await bear_node(state, llm=mock_llm)
        assert result["bear_signal"] == 0.5  # 보수적 기본값


class TestJudgeAgent:
    async def test_buy_decision(self, mock_llm_judge):
        """매수 결정 반환"""
        state = make_state(bull_signal=0.8, bear_signal=0.2)
        result = await judge_node(state, llm=mock_llm_judge)
        assert result["judge_decision"] in ("BUY", "SELL", "HOLD")
        assert 0.0 <= result["judge_confidence"] <= 1.0

    async def test_kill_switch_forces_hold(self):
        """킬스위치 활성화 시 LLM 호출 없이 HOLD 반환"""
        mock_llm = AsyncMock()
        state = make_state(
            kill_switch_active=True,
            kill_switch_reason="테스트 킬스위치",
        )
        result = await judge_node(state, llm=mock_llm)

        assert result["judge_decision"] == "HOLD"
        assert result["judge_confidence"] == 1.0
        mock_llm.ainvoke.assert_not_called()  # LLM 호출 안 함

    async def test_position_size_capped(self):
        """투자 비중 30% 초과 방지"""
        mock_llm = AsyncMock()
        mock_llm.ainvoke.return_value = MagicMock(
            content='{"decision": "BUY", "confidence": 0.9, "position_size_pct": 80.0, "reasoning": "all in"}'
        )
        state = make_state()
        result = await judge_node(state, llm=mock_llm)
        assert result["position_size_pct"] <= 30.0

    async def test_handles_invalid_decision(self):
        """잘못된 결정값 → HOLD로 폴백"""
        mock_llm = AsyncMock()
        mock_llm.ainvoke.return_value = MagicMock(
            content='{"decision": "MAYBE", "confidence": 0.5, "position_size_pct": 10.0, "reasoning": "unsure"}'
        )
        state = make_state()
        result = await judge_node(state, llm=mock_llm)
        assert result["judge_decision"] == "HOLD"
