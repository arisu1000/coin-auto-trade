"""시스템 내부 이벤트 타입"""
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class MarketDataUpdated:
    market: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class OrderPlaced:
    market: str
    side: str
    price: float
    volume: float
    uuid: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class OrderFilled:
    uuid: str
    market: str
    side: str
    filled_price: float
    filled_volume: float
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class PortfolioSnapshot:
    total_equity: float
    cash_krw: float
    coin_value: float
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
