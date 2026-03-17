"""
업비트 REST API 클라이언트

- JWT 인증 (HS256)
- Token Bucket Rate Limiting
- Exponential Backoff 재시도
- 응답 → 도메인 모델 변환
"""
import hashlib
import uuid as uuid_mod
from datetime import datetime
from typing import Any
from urllib.parse import urlencode

import aiohttp
import structlog
from jose import jwt

from src.config.settings import Settings
from src.exchange.models import Balance, Candle, Order, OrderBook, OrderBookUnit
from src.exchange.rate_limiter import (
    ExponentialBackoff,
    RateLimitError,
    RetryableError,
    TokenBucket,
)

logger = structlog.get_logger(__name__)


class UpbitClient:
    """
    업비트 API v1 비동기 클라이언트

    사용법:
        async with UpbitClient(settings) as client:
            candles = await client.get_candles_minutes("KRW-BTC", unit=1, count=200)
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._access_key = settings.upbit_access_key.get_secret_value()
        self._secret_key = settings.upbit_secret_key.get_secret_value()
        self._base_url = settings.upbit_base_url
        self._session: aiohttp.ClientSession | None = None

        # Exchange API용 버킷 (초당 7회, 버스트 10)
        self._exchange_bucket = TokenBucket(
            rate=settings.rate_limit_rps,
            capacity=settings.rate_limit_burst,
        )
        # Quotation API용 버킷 (초당 9회, 버스트 20)
        self._quotation_bucket = TokenBucket(rate=9.0, capacity=20)
        self._backoff = ExponentialBackoff(
            base_seconds=settings.backoff_base_seconds,
            max_seconds=settings.backoff_max_seconds,
            max_retries=settings.backoff_max_retries,
        )

    async def __aenter__(self) -> "UpbitClient":
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=10),
            headers={"Content-Type": "application/json"},
        )
        return self

    async def __aexit__(self, *args: Any) -> None:
        if self._session:
            await self._session.close()

    # ─── Public Quotation API ───────────────────────────────────────────

    async def get_candles_minutes(
        self, market: str, unit: int = 1, count: int = 200, to: str | None = None
    ) -> list[Candle]:
        """분봉 캔들 조회 (최대 200개)"""
        params: dict[str, Any] = {"market": market, "count": min(count, 200)}
        if to:
            params["to"] = to

        data = await self._get(
            f"/candles/minutes/{unit}", params=params, use_exchange_bucket=False
        )
        return [self._parse_candle(c) for c in data]

    async def get_orderbook(self, markets: list[str]) -> list[OrderBook]:
        """호가창 조회"""
        params = {"markets": ",".join(markets)}
        data = await self._get("/orderbook", params=params, use_exchange_bucket=False)
        return [self._parse_orderbook(ob) for ob in data]

    async def get_markets(self, krw_only: bool = True) -> list[dict]:
        """
        업비트 전체 마켓 목록 조회

        Returns:
            [{"market": "KRW-BTC", "korean_name": "비트코인", ...}, ...]
        """
        data = await self._get("/market/all", params={}, use_exchange_bucket=False)
        if krw_only:
            return [m for m in data if m["market"].startswith("KRW-")]
        return data

    async def get_ticker(self, markets: list[str]) -> list[dict]:
        """현재가 조회"""
        params = {"markets": ",".join(markets)}
        return await self._get("/ticker", params=params, use_exchange_bucket=False)

    # ─── Authenticated Exchange API ────────────────────────────────────

    async def get_balances(self) -> list[Balance]:
        """계좌 잔고 조회"""
        data = await self._get("/accounts", use_exchange_bucket=True)
        return [self._parse_balance(b) for b in data]

    async def place_order(
        self,
        market: str,
        side: str,
        volume: float | None = None,
        price: float | None = None,
        ord_type: str = "limit",
    ) -> Order:
        """
        주문 발주

        Args:
            market: 마켓 코드 (예: KRW-BTC)
            side: "bid" (매수) | "ask" (매도)
            volume: 주문 수량
            price: 주문 가격 (시장가 매수 시 None)
            ord_type: "limit" | "price" (시장가 매수) | "market" (시장가 매도)
        """
        body: dict[str, Any] = {"market": market, "side": side, "ord_type": ord_type}
        if volume is not None:
            body["volume"] = str(volume)
        if price is not None:
            body["price"] = str(price)

        data = await self._post("/orders", body=body)
        return self._parse_order(data)

    async def cancel_order(self, uuid: str) -> Order:
        """주문 취소"""
        data = await self._delete(f"/order?uuid={uuid}")
        return self._parse_order(data)

    async def get_order(self, uuid: str) -> Order:
        """주문 상태 조회"""
        data = await self._get(f"/order", params={"uuid": uuid}, use_exchange_bucket=True)
        return self._parse_order(data)

    # ─── HTTP 메서드 ────────────────────────────────────────────────────

    async def _get(
        self,
        path: str,
        params: dict | None = None,
        use_exchange_bucket: bool = True,
    ) -> Any:
        bucket = self._exchange_bucket if use_exchange_bucket else self._quotation_bucket
        await bucket.acquire()

        async def _call():
            headers = {}
            if use_exchange_bucket:
                headers["Authorization"] = f"Bearer {self._make_jwt(params or {})}"
            url = f"{self._base_url}{path}"
            async with self._session.get(url, params=params, headers=headers) as resp:
                return await self._handle_response(resp)

        return await self._backoff.execute(_call)

    async def _post(self, path: str, body: dict) -> Any:
        await self._exchange_bucket.acquire()

        async def _call():
            headers = {"Authorization": f"Bearer {self._make_jwt(body)}"}
            url = f"{self._base_url}{path}"
            async with self._session.post(url, json=body, headers=headers) as resp:
                return await self._handle_response(resp)

        return await self._backoff.execute(_call)

    async def _delete(self, path: str) -> Any:
        await self._exchange_bucket.acquire()

        async def _call():
            # DELETE의 query params도 JWT에 포함
            query = path.split("?", 1)[1] if "?" in path else ""
            params = dict(p.split("=") for p in query.split("&") if "=" in p)
            headers = {"Authorization": f"Bearer {self._make_jwt(params)}"}
            url = f"{self._base_url}{path}"
            async with self._session.delete(url, headers=headers) as resp:
                return await self._handle_response(resp)

        return await self._backoff.execute(_call)

    async def _handle_response(self, resp: aiohttp.ClientResponse) -> Any:
        # Remaining-Req 헤더로 잔여 호출 수 모니터링
        remaining = resp.headers.get("Remaining-Req", "")
        if remaining:
            parts = {k: v for kv in remaining.split("; ") for k, v in [kv.split("=")]}
            sec_remaining = int(parts.get("sec", 999))
            if sec_remaining < 3:
                logger.warning("api_rate_limit_low", remaining=sec_remaining)

        if resp.status == 429:
            retry_after_str = resp.headers.get("Retry-After", "1")
            retry_after = float(retry_after_str)
            raise RateLimitError(f"429 Too Many Requests", retry_after=retry_after)

        if resp.status >= 500:
            text = await resp.text()
            raise RetryableError(f"Server error {resp.status}: {text}")

        if resp.status >= 400:
            text = await resp.text()
            raise ValueError(f"Client error {resp.status}: {text}")

        return await resp.json()

    def _make_jwt(self, params: dict) -> str:
        """업비트 JWT 인증 토큰 생성 (HS256)"""
        payload: dict[str, Any] = {
            "access_key": self._access_key,
            "nonce": str(uuid_mod.uuid4()),
        }
        if params:
            query_string = urlencode(params)
            m = hashlib.sha512()
            m.update(query_string.encode())
            payload["query_hash"] = m.hexdigest()
            payload["query_hash_alg"] = "SHA512"

        return jwt.encode(payload, self._secret_key, algorithm="HS256")

    # ─── 파싱 헬퍼 ─────────────────────────────────────────────────────

    @staticmethod
    def _parse_candle(data: dict) -> Candle:
        return Candle(
            market=data["market"],
            timestamp=datetime.fromisoformat(
                data["candle_date_time_utc"].replace("T", " ")
            ),
            open=float(data["opening_price"]),
            high=float(data["high_price"]),
            low=float(data["low_price"]),
            close=float(data["trade_price"]),
            volume=float(data["candle_acc_trade_volume"]),
        )

    @staticmethod
    def _parse_orderbook(data: dict) -> OrderBook:
        units = [
            OrderBookUnit(
                ask_price=float(u["ask_price"]),
                bid_price=float(u["bid_price"]),
                ask_size=float(u["ask_size"]),
                bid_size=float(u["bid_size"]),
            )
            for u in data.get("orderbook_units", [])
        ]
        return OrderBook(
            market=data["market"],
            timestamp=data["timestamp"],
            total_ask_size=float(data["total_ask_size"]),
            total_bid_size=float(data["total_bid_size"]),
            units=units,
        )

    @staticmethod
    def _parse_balance(data: dict) -> Balance:
        return Balance(
            currency=data["currency"],
            balance=float(data["balance"]),
            locked=float(data["locked"]),
            avg_buy_price=float(data["avg_buy_price"]),
        )

    @staticmethod
    def _parse_order(data: dict) -> Order:
        return Order(
            uuid=data["uuid"],
            market=data["market"],
            side=data["side"],
            ord_type=data["ord_type"],
            price=float(data.get("price") or 0),
            volume=float(data.get("volume") or 0),
            executed_volume=float(data.get("executed_volume") or 0),
            state=data["state"],
            created_at=datetime.fromisoformat(
                data["created_at"].replace("T", " ").split("+")[0]
            ),
            remaining_fee=float(data.get("remaining_fee") or 0),
            paid_fee=float(data.get("paid_fee") or 0),
        )
