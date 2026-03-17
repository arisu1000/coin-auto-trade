"""
공유 픽스처 및 Mock 팩토리

모든 테스트는 이 픽스처를 통해 외부 의존성(API, LLM)을 차단한다.
실제 업비트 API 호출이나 OpenAI API 호출은 절대 발생하지 않아야 한다.
"""
import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest
import pytest_asyncio

from src.config.settings import Settings
from src.exchange.models import Balance, Candle, Order, OrderBook, OrderBookUnit
from src.persistence.database import Database
from src.persistence.migrations import run_migrations


# ── 설정 픽스처 ────────────────────────────────────────────────────

@pytest.fixture
def settings() -> Settings:
    """테스트용 설정 (인메모리 DB, 모의매매 모드)"""
    return Settings(
        upbit_access_key="test-access-key",
        upbit_secret_key="test-secret-key",
        openai_api_key="sk-test-openai-key",
        telegram_bot_token="123456789:test-bot-token",
        telegram_chat_id="999999999",
        db_path=":memory:",
        trading_mode="paper",
        log_level="DEBUG",
    )


# ── 데이터베이스 픽스처 ─────────────────────────────────────────────

@pytest_asyncio.fixture
async def db(settings: Settings) -> Database:
    """인메모리 SQLite DB (테스트 후 자동 소멸)"""
    database = Database(settings.db_path)
    await database.connect()
    await run_migrations(database)
    yield database
    await database.close()


# ── 가짜 시장 데이터 ────────────────────────────────────────────────

def make_fake_candles(count: int = 100, base_price: float = 50_000_000) -> list[Candle]:
    """테스트용 가짜 캔들 데이터 생성"""
    candles = []
    price = base_price
    for i in range(count):
        # 간단한 랜덤 워크
        change = (i % 7 - 3) * 100_000
        price = max(1_000, price + change)
        candles.append(Candle(
            market="KRW-BTC",
            timestamp=datetime(2024, 1, 1, i // 60, i % 60, tzinfo=timezone.utc),
            open=price - 50_000,
            high=price + 100_000,
            low=price - 100_000,
            close=price,
            volume=0.5 + (i % 3) * 0.2,
        ))
    return candles


def make_fake_orderbook(market: str = "KRW-BTC", price: float = 50_000_000) -> OrderBook:
    """테스트용 가짜 호가창"""
    units = [
        OrderBookUnit(
            ask_price=price * (1 + 0.001 * (i + 1)),
            bid_price=price * (1 - 0.001 * (i + 1)),
            ask_size=0.1 * (i + 1),
            bid_size=0.1 * (i + 1),
        )
        for i in range(5)
    ]
    return OrderBook(
        market=market,
        timestamp=1_700_000_000_000,
        total_ask_size=sum(u.ask_size for u in units),
        total_bid_size=sum(u.bid_size for u in units),
        units=units,
    )


def make_fake_order(
    uuid: str = "test-uuid-001",
    market: str = "KRW-BTC",
    side: str = "bid",
    state: str = "done",
) -> Order:
    """테스트용 가짜 주문"""
    return Order(
        uuid=uuid,
        market=market,
        side=side,
        ord_type="limit",
        price=50_000_000.0,
        volume=0.001,
        executed_volume=0.001,
        state=state,
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )


def make_fake_balances() -> list[Balance]:
    """테스트용 가짜 잔고"""
    return [
        Balance(currency="KRW", balance=1_000_000, locked=0, avg_buy_price=1),
        Balance(currency="BTC", balance=0.001, locked=0, avg_buy_price=50_000_000),
    ]


# ── UpbitClient Mock ────────────────────────────────────────────────

@pytest.fixture
def mock_upbit():
    """완전히 Mock된 UpbitClient"""
    client = AsyncMock()
    client.get_candles_minutes.return_value = make_fake_candles(100)
    client.get_orderbook.return_value = [make_fake_orderbook()]
    client.get_balances.return_value = make_fake_balances()
    client.place_order.return_value = make_fake_order()
    client.cancel_order.return_value = make_fake_order(state="cancel")
    client.get_order.return_value = make_fake_order(state="done")
    client.get_ticker.return_value = [{"trade_price": "50000000", "market": "KRW-BTC"}]

    # context manager 지원
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    return client


# ── LLM Mock ────────────────────────────────────────────────────────

@pytest.fixture
def mock_llm_bull():
    """강세장 에이전트용 Mock LLM"""
    mock = AsyncMock()
    mock.ainvoke.return_value = MagicMock(
        content='{"signal": 0.7, "reasoning": "RSI 과매도 회복 + MACD 골든크로스", "key_indicators": ["RSI", "MACD"]}'
    )
    return mock


@pytest.fixture
def mock_llm_bear():
    """약세장 에이전트용 Mock LLM"""
    mock = AsyncMock()
    mock.ainvoke.return_value = MagicMock(
        content='{"signal": 0.3, "reasoning": "강한 매도 저항 없음", "risk_factors": []}'
    )
    return mock


@pytest.fixture
def mock_llm_judge():
    """심판 에이전트용 Mock LLM"""
    mock = AsyncMock()
    mock.ainvoke.return_value = MagicMock(
        content='{"decision": "BUY", "confidence": 0.75, "position_size_pct": 20.0, "reasoning": "강세 우세"}'
    )
    return mock


# ── 공통 DataFrame 픽스처 ────────────────────────────────────────────

@pytest.fixture
def sample_ohlcv_df() -> pd.DataFrame:
    """기술적 지표 계산에 충분한 길이의 샘플 OHLCV DataFrame"""
    n = 200
    base = 50_000_000
    closes = [base + (i - 100) * 50_000 for i in range(n)]
    return pd.DataFrame({
        "open":   [c - 10_000 for c in closes],
        "high":   [c + 50_000 for c in closes],
        "low":    [c - 50_000 for c in closes],
        "close":  closes,
        "volume": [0.5 + (i % 5) * 0.1 for i in range(n)],
    }, index=pd.date_range("2024-01-01", periods=n, freq="1min"))
