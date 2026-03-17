"""
업비트 WebSocket 실시간 호가창 스트리밍

- 자동 재연결 (지수 백오프)
- 비동기 제너레이터로 OrderBook 이벤트 스트리밍
- asyncio.Queue 기반 내부 버퍼링
"""
import asyncio
import json
import uuid
from typing import AsyncGenerator

import structlog
import websockets
from websockets.exceptions import ConnectionClosed

from src.exchange.models import OrderBook, OrderBookUnit

logger = structlog.get_logger(__name__)


class OrderbookStream:
    """
    업비트 웹소켓 호가창 실시간 스트리밍

    사용법:
        stream = OrderbookStream(ws_url="wss://api.upbit.com/websocket/v1")
        async with stream:
            async for orderbook in stream.stream(["KRW-BTC", "KRW-ETH"]):
                process(orderbook)
    """

    def __init__(self, ws_url: str, queue_size: int = 100) -> None:
        self._url = ws_url
        self._queue: asyncio.Queue[OrderBook] = asyncio.Queue(maxsize=queue_size)
        self._markets: list[str] = []
        self._ws = None
        self._running = False
        self._reconnect_task: asyncio.Task | None = None

    async def __aenter__(self) -> "OrderbookStream":
        self._running = True
        return self

    async def __aexit__(self, *args) -> None:
        self._running = False
        if self._reconnect_task:
            self._reconnect_task.cancel()
        if self._ws:
            await self._ws.close()

    async def start(self, markets: list[str]) -> None:
        """스트리밍 시작 (백그라운드 태스크)"""
        self._markets = markets
        self._reconnect_task = asyncio.create_task(self._reconnect_loop())

    async def stream(self, markets: list[str]) -> AsyncGenerator[OrderBook, None]:
        """호가창 데이터를 AsyncGenerator로 반환"""
        await self.start(markets)
        while self._running:
            try:
                orderbook = await asyncio.wait_for(self._queue.get(), timeout=30.0)
                yield orderbook
            except asyncio.TimeoutError:
                logger.warning("websocket_stream_timeout")
                continue

    async def _reconnect_loop(self) -> None:
        """자동 재연결 루프"""
        attempt = 0
        while self._running:
            try:
                await self._connect_and_receive()
                attempt = 0  # 성공 시 시도 카운터 리셋
            except ConnectionClosed as e:
                if not self._running:
                    break
                delay = min(2 ** attempt, 30)
                logger.warning(
                    "websocket_disconnected",
                    code=e.code,
                    attempt=attempt,
                    reconnect_in=delay,
                )
                await asyncio.sleep(delay)
                attempt += 1
            except Exception as e:
                if not self._running:
                    break
                delay = min(2 ** attempt, 30)
                logger.error("websocket_error", error=str(e), reconnect_in=delay)
                await asyncio.sleep(delay)
                attempt += 1

    async def _connect_and_receive(self) -> None:
        """WebSocket 연결 후 메시지 수신"""
        async with websockets.connect(self._url) as ws:
            self._ws = ws
            logger.info("websocket_connected", url=self._url)

            # 구독 메시지 전송
            subscribe_msg = [
                {"ticket": str(uuid.uuid4())},
                {"type": "orderbook", "codes": self._markets},
                {"format": "SIMPLE"},  # 간소화 포맷
            ]
            await ws.send(json.dumps(subscribe_msg))
            logger.info("websocket_subscribed", markets=self._markets)

            # 메시지 수신 루프
            async for raw_msg in ws:
                if not self._running:
                    break
                try:
                    data = json.loads(raw_msg)
                    orderbook = self._parse_message(data)
                    if orderbook:
                        # 큐가 가득 찬 경우 가장 오래된 항목 제거 후 삽입
                        if self._queue.full():
                            try:
                                self._queue.get_nowait()
                            except asyncio.QueueEmpty:
                                pass
                        await self._queue.put(orderbook)
                except Exception as e:
                    logger.error("websocket_parse_error", error=str(e))

    @staticmethod
    def _parse_message(data: dict) -> OrderBook | None:
        """WebSocket 메시지 → OrderBook 모델 변환"""
        if data.get("ty") != "orderbook":
            return None

        units = []
        for unit in data.get("obu", []):
            units.append(
                OrderBookUnit(
                    ask_price=float(unit.get("ap", 0)),
                    bid_price=float(unit.get("bp", 0)),
                    ask_size=float(unit.get("as", 0)),
                    bid_size=float(unit.get("bs", 0)),
                )
            )

        return OrderBook(
            market=data["cd"],
            timestamp=data["tms"],
            total_ask_size=float(data.get("tas", 0)),
            total_bid_size=float(data.get("tbs", 0)),
            units=units,
        )
