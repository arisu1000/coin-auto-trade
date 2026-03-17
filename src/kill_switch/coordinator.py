"""
이중 킬 스위치 코디네이터

거래 파이프라인의 최후 방어선.
어떠한 AI 판단도 이 게이트를 통과하지 못하면 실행될 수 없다.

킬 스위치 종류:
1. 매크로 킬 스위치: 포트폴리오 전체 낙폭 임계치 초과 → 모든 거래 차단
2. 마이크로 킬 스위치: 개별 코인 손절 임계치 초과 → 해당 코인 즉시 청산
3. 수동 킬 스위치: 텔레그램 /halt 명령 → 관리자 직접 발동
"""
import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class KillSwitchEvent:
    event_type: str       # "macro" | "micro" | "manual" | "reset"
    reason: str
    triggered_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    market: str | None = None
    drawdown_pct: float = 0.0


class KillSwitchCoordinator:
    """
    모든 킬 스위치의 상태를 중앙에서 관리한다.

    트레이더가 매 틱마다 is_halted를 확인하고,
    True이면 어떤 주문도 발주하지 않는다.
    """

    def __init__(
        self,
        macro_threshold_pct: float = 15.0,
        micro_threshold_pct: float = 3.0,
    ) -> None:
        self._macro_threshold = macro_threshold_pct
        self._micro_threshold = micro_threshold_pct

        self._macro_active: bool = False
        self._micro_active_markets: set[str] = set()
        self._manual_halt: bool = False

        self._peak_equity: float = 0.0
        self._events: list[KillSwitchEvent] = []
        self._event_callbacks: list = []

    # ── 상태 쿼리 ────────────────────────────────────────────────────

    @property
    def is_halted(self) -> bool:
        """전체 매매 중단 여부"""
        return self._macro_active or self._manual_halt

    def is_market_blocked(self, market: str) -> bool:
        """특정 코인 거래 차단 여부"""
        return self.is_halted or market in self._micro_active_markets

    @property
    def status(self) -> dict:
        return {
            "is_halted": self.is_halted,
            "macro_active": self._macro_active,
            "manual_halt": self._manual_halt,
            "blocked_markets": list(self._micro_active_markets),
            "peak_equity": self._peak_equity,
            "recent_events": [
                {
                    "type": e.event_type,
                    "reason": e.reason,
                    "time": e.triggered_at.isoformat(),
                }
                for e in self._events[-5:]
            ],
        }

    # ── 킬 스위치 발동 ─────────────────────────────────────────────

    async def check_macro(self, current_equity: float) -> bool:
        """
        포트폴리오 낙폭 확인 (매 모니터링 틱마다 호출)

        낙폭 = (최고점 - 현재) / 최고점
        """
        if current_equity > self._peak_equity:
            self._peak_equity = current_equity

        if self._peak_equity == 0:
            return False

        drawdown_pct = (self._peak_equity - current_equity) / self._peak_equity * 100

        if drawdown_pct >= self._macro_threshold and not self._macro_active:
            await self._trigger_macro(drawdown_pct)

        return self._macro_active

    async def check_micro(self, market: str, entry_price: float, current_price: float) -> bool:
        """
        개별 포지션 손절 확인

        손절률 = (진입가 - 현재가) / 진입가 (매수 포지션 기준)
        """
        if current_price >= entry_price:
            return False

        loss_pct = (entry_price - current_price) / entry_price * 100

        if loss_pct >= self._micro_threshold and market not in self._micro_active_markets:
            await self._trigger_micro(market, loss_pct)

        return market in self._micro_active_markets

    async def trigger_manual_halt(self, reason: str = "관리자 수동 발동") -> None:
        """텔레그램 /halt 명령으로 수동 발동"""
        self._manual_halt = True
        event = KillSwitchEvent(event_type="manual", reason=reason)
        self._events.append(event)
        logger.critical("kill_switch_manual_halt", reason=reason)
        await self._notify(event)

    async def reset(self, confirm: bool = False) -> None:
        """모든 킬 스위치 해제 (명시적 confirm 필요)"""
        if not confirm:
            raise ValueError("킬 스위치 해제는 confirm=True가 필요합니다")
        self._macro_active = False
        self._manual_halt = False
        self._micro_active_markets.clear()
        event = KillSwitchEvent(event_type="reset", reason="관리자 리셋")
        self._events.append(event)
        logger.warning("kill_switch_reset")
        await self._notify(event)

    def register_callback(self, callback) -> None:
        """킬 스위치 이벤트 콜백 등록 (텔레그램 알림용)"""
        self._event_callbacks.append(callback)

    # ── 내부 메서드 ──────────────────────────────────────────────────

    async def _trigger_macro(self, drawdown_pct: float) -> None:
        self._macro_active = True
        event = KillSwitchEvent(
            event_type="macro",
            reason=f"포트폴리오 낙폭 {drawdown_pct:.2f}% - 임계치 {self._macro_threshold}% 초과",
            drawdown_pct=drawdown_pct,
        )
        self._events.append(event)
        logger.critical(
            "kill_switch_macro_triggered",
            drawdown_pct=drawdown_pct,
            threshold=self._macro_threshold,
        )
        await self._notify(event)

    async def _trigger_micro(self, market: str, loss_pct: float) -> None:
        self._micro_active_markets.add(market)
        event = KillSwitchEvent(
            event_type="micro",
            reason=f"{market} 손절 {loss_pct:.2f}% - 임계치 {self._micro_threshold}% 초과",
            market=market,
        )
        self._events.append(event)
        logger.warning(
            "kill_switch_micro_triggered",
            market=market,
            loss_pct=loss_pct,
        )
        await self._notify(event)

    async def _notify(self, event: KillSwitchEvent) -> None:
        for cb in self._event_callbacks:
            try:
                if asyncio.iscoroutinefunction(cb):
                    await cb(event)
                else:
                    cb(event)
            except Exception as e:
                logger.error("kill_switch_callback_error", error=str(e))
