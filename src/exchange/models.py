from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Candle:
    market: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    trade_price: float = 0.0


@dataclass
class OrderBookUnit:
    ask_price: float
    bid_price: float
    ask_size: float
    bid_size: float


@dataclass
class OrderBook:
    market: str
    timestamp: int
    total_ask_size: float
    total_bid_size: float
    units: list[OrderBookUnit] = field(default_factory=list)

    @property
    def best_ask(self) -> float:
        return self.units[0].ask_price if self.units else 0.0

    @property
    def best_bid(self) -> float:
        return self.units[0].bid_price if self.units else 0.0

    @property
    def spread(self) -> float:
        if not self.units:
            return 0.0
        return self.best_ask - self.best_bid

    @property
    def bid_ask_ratio(self) -> float:
        """총 매수 잔량 / 총 매도 잔량 (>1 이면 매수세 우세)"""
        if self.total_ask_size == 0:
            return float("inf")
        return self.total_bid_size / self.total_ask_size


@dataclass
class Balance:
    currency: str
    balance: float
    locked: float
    avg_buy_price: float

    @property
    def available(self) -> float:
        return self.balance - self.locked


@dataclass
class Order:
    uuid: str
    market: str
    side: str          # "bid" | "ask"
    ord_type: str      # "limit" | "price" | "market"
    price: float
    volume: float
    executed_volume: float
    state: str         # "wait" | "done" | "cancel"
    created_at: datetime
    remaining_fee: float = 0.0
    paid_fee: float = 0.0

    @property
    def is_done(self) -> bool:
        return self.state == "done"

    @property
    def is_pending(self) -> bool:
        return self.state == "wait"
