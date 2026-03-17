"""
킬 스위치 단위 테스트

매크로/마이크로 킬 스위치 발동 조건 및 이벤트 콜백 검증
"""
import pytest

from src.kill_switch.coordinator import KillSwitchCoordinator


@pytest.fixture
def coordinator():
    return KillSwitchCoordinator(macro_threshold_pct=10.0, micro_threshold_pct=3.0)


class TestMacroKillSwitch:
    async def test_no_trigger_below_threshold(self, coordinator):
        """낙폭이 임계치 미만이면 발동 안 함"""
        await coordinator.check_macro(1_000_000)
        await coordinator.check_macro(950_000)  # 5% 낙폭
        assert not coordinator.is_halted

    async def test_triggers_at_threshold(self, coordinator):
        """낙폭이 임계치 이상이면 발동"""
        await coordinator.check_macro(1_000_000)
        await coordinator.check_macro(880_000)  # 12% 낙폭 > 10%
        assert coordinator.is_halted
        assert coordinator.status["macro_active"]

    async def test_peak_tracking(self, coordinator):
        """최고점 갱신 추적"""
        await coordinator.check_macro(500_000)
        await coordinator.check_macro(1_000_000)  # 최고점 갱신
        await coordinator.check_macro(950_000)    # 5% 낙폭 (1,000,000 기준)
        assert not coordinator.is_halted

    async def test_event_callback_fired(self, coordinator):
        """발동 시 콜백 호출"""
        events = []
        coordinator.register_callback(lambda e: events.append(e))

        await coordinator.check_macro(1_000_000)
        await coordinator.check_macro(850_000)  # 15% 낙폭

        assert len(events) == 1
        assert events[0].event_type == "macro"

    async def test_no_duplicate_trigger(self, coordinator):
        """이미 발동된 경우 중복 발동 안 함"""
        events = []
        coordinator.register_callback(lambda e: events.append(e))

        await coordinator.check_macro(1_000_000)
        await coordinator.check_macro(800_000)
        await coordinator.check_macro(700_000)  # 이미 발동된 상태

        macro_events = [e for e in events if e.event_type == "macro"]
        assert len(macro_events) == 1


class TestMicroKillSwitch:
    async def test_no_trigger_for_profitable(self, coordinator):
        """수익 중인 포지션은 발동 안 함"""
        result = await coordinator.check_micro("KRW-BTC", 50_000_000, 55_000_000)
        assert not result
        assert "KRW-BTC" not in coordinator.status["blocked_markets"]

    async def test_triggers_at_stop_loss(self, coordinator):
        """손절 임계치 이상 하락 시 발동"""
        result = await coordinator.check_micro("KRW-BTC", 50_000_000, 48_000_000)  # 4% 하락
        assert result
        assert "KRW-BTC" in coordinator.status["blocked_markets"]

    async def test_different_markets_independent(self, coordinator):
        """서로 다른 마켓은 독립적으로 동작"""
        await coordinator.check_micro("KRW-BTC", 50_000_000, 48_000_000)  # BTC 발동
        assert "KRW-BTC" in coordinator.status["blocked_markets"]
        assert "KRW-ETH" not in coordinator.status["blocked_markets"]

    async def test_is_market_blocked(self, coordinator):
        """특정 마켓 차단 여부 확인"""
        assert not coordinator.is_market_blocked("KRW-BTC")
        await coordinator.check_micro("KRW-BTC", 50_000_000, 48_000_000)
        assert coordinator.is_market_blocked("KRW-BTC")


class TestManualHalt:
    async def test_manual_halt(self, coordinator):
        """수동 킬스위치 발동"""
        await coordinator.trigger_manual_halt("테스트")
        assert coordinator.is_halted
        assert coordinator.status["manual_halt"]

    async def test_reset_requires_confirm(self, coordinator):
        """confirm=False이면 해제 거부"""
        await coordinator.trigger_manual_halt()
        with pytest.raises(ValueError):
            await coordinator.reset(confirm=False)
        assert coordinator.is_halted

    async def test_reset_with_confirm(self, coordinator):
        """confirm=True이면 해제 성공"""
        await coordinator.trigger_manual_halt()
        await coordinator.reset(confirm=True)
        assert not coordinator.is_halted
