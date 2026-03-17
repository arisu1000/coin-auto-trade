"""
트레이딩 메인 오케스트레이터

asyncio.gather()로 다음 4개 코루틴을 동시 실행:
1. _market_loop: 시장 데이터 수집 + 기술적 지표 계산
2. _strategy_loop: AI 에이전트 워크플로우 → 주문 실행
3. _monitor_loop: 포트폴리오 스냅샷 + 킬스위치 감시
4. _telegram_bot: 원격 제어 인터페이스

각 루프는 독립적으로 동작하며 공유 상태를 통해 소통한다.
"""
import asyncio
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import structlog
import talib

from src.agents.state import AgentState, MarketSnapshot
from src.agents.workflow import build_workflow
from src.bot.telegram_bot import TradingBot
from src.config.settings import Settings
from src.exchange.upbit_client import UpbitClient
from src.exchange.websocket_stream import OrderbookStream
from src.kill_switch.coordinator import KillSwitchCoordinator
from src.persistence.database import Database
from src.persistence.migrations import run_migrations
from src.persistence.repositories.portfolio import PortfolioRepository
from src.persistence.repositories.trades import TradeRepository
from src.strategy.manager import StrategyManager

logger = structlog.get_logger(__name__)


class Trader:
    """
    자동매매 메인 클래스

    사용법:
        trader = Trader(settings)
        await trader.run()  # 무한 루프
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._db: Database | None = None
        self._upbit: UpbitClient | None = None
        self._orderbook_stream: OrderbookStream | None = None
        self._coordinator: KillSwitchCoordinator | None = None
        self._strategy_manager: StrategyManager | None = None
        self._workflow = None
        self._bot: TradingBot | None = None

        # 공유 상태 (코루틴 간 통신)
        self._latest_candles: dict[str, pd.DataFrame] = {}
        self._latest_orderbooks: dict = {}
        self._active_orders: dict = {}  # uuid → Order
        self._running = False

    async def run(self) -> None:
        """메인 실행 진입점"""
        await self._initialize()
        self._running = True
        logger.info("trader_started", mode=self._settings.trading_mode)

        try:
            await asyncio.gather(
                self._market_loop(),
                self._strategy_loop(),
                self._monitor_loop(),
                self._bot.start() if self._bot else asyncio.sleep(0),
            )
        except asyncio.CancelledError:
            logger.info("trader_cancelled")
        except Exception as e:
            logger.critical("trader_fatal_error", error=str(e))
            raise
        finally:
            await self._shutdown()

    # ── 초기화 ───────────────────────────────────────────────────────

    async def _initialize(self) -> None:
        """모든 서브시스템 초기화"""
        # 데이터베이스
        self._db = Database(self._settings.db_path)
        await self._db.connect()
        await run_migrations(self._db)

        # 업비트 클라이언트
        self._upbit = UpbitClient(self._settings)
        self._upbit_ctx = await self._upbit.__aenter__()

        # 킬 스위치
        self._coordinator = KillSwitchCoordinator(
            macro_threshold_pct=self._settings.macro_max_drawdown_pct,
            micro_threshold_pct=self._settings.micro_stop_loss_pct,
        )

        # 전략 매니저
        strategy_dir = Path("src/strategy")
        self._strategy_manager = StrategyManager(strategy_dir)
        self._strategy_manager.activate(self._settings.default_strategy)

        # LangGraph 워크플로우
        self._workflow = build_workflow(self._settings, db_path=self._settings.db_path)

        # WebSocket 스트림
        self._orderbook_stream = OrderbookStream(self._settings.upbit_ws_url)

        # 텔레그램 봇
        self._bot = TradingBot(
            settings=self._settings,
            dependencies={
                "db": self._db,
                "coordinator": self._coordinator,
                "trader": self,
                "strategy_manager": self._strategy_manager,
            },
        )

        # 킬 스위치 → 텔레그램 알림 연결
        self._coordinator.register_callback(
            lambda e: asyncio.create_task(
                self._bot.send_alert(f"🚨 킬 스위치 발동\n{e.reason}")
            )
        )

        logger.info("trader_initialized")

    async def _shutdown(self) -> None:
        """정상 종료"""
        self._running = False
        if self._bot:
            await self._bot.stop()
        if self._upbit:
            await self._upbit.__aexit__(None, None, None)
        if self._db:
            await self._db.close()
        logger.info("trader_shutdown_complete")

    # ── 시장 데이터 루프 ─────────────────────────────────────────────

    async def _market_loop(self) -> None:
        """캔들 데이터 갱신 루프 (매 interval마다)"""
        while self._running:
            try:
                for market in self._settings.markets_list:
                    candles = await self._upbit_ctx.get_candles_minutes(
                        market, unit=1, count=100
                    )
                    if candles:
                        df = self._candles_to_df(candles)
                        df = self._compute_indicators(df)
                        self._latest_candles[market] = df

                await asyncio.sleep(self._settings.trade_interval_seconds)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("market_loop_error", error=str(e))
                await asyncio.sleep(5)

    # ── 전략/에이전트 루프 ───────────────────────────────────────────

    async def _strategy_loop(self) -> None:
        """AI 에이전트 분석 → 주문 실행 루프"""
        # 첫 데이터 수집 대기
        await asyncio.sleep(self._settings.trade_interval_seconds * 2)

        while self._running:
            try:
                for market in self._settings.markets_list:
                    if self._coordinator.is_market_blocked(market):
                        continue

                    df = self._latest_candles.get(market)
                    if df is None or len(df) < 50:
                        continue

                    snapshot = self._build_snapshot(market, df)
                    state = self._build_initial_state(snapshot)

                    # LangGraph 워크플로우 실행
                    result = await self._workflow.ainvoke(
                        state,
                        config={"configurable": {"thread_id": f"main_{market}"}},
                    )

                    decision = result.get("judge_decision", "HOLD")
                    confidence = result.get("judge_confidence", 0.0)
                    position_size_pct = result.get("position_size_pct", 0.0)

                    logger.info(
                        "agent_decision",
                        market=market,
                        decision=decision,
                        confidence=confidence,
                        position_size_pct=position_size_pct,
                    )

                    if decision != "HOLD" and confidence > 0.6:
                        await self._execute_decision(
                            market, decision, position_size_pct, result
                        )

                await asyncio.sleep(self._settings.trade_interval_seconds)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("strategy_loop_error", error=str(e))
                await asyncio.sleep(10)

    # ── 모니터링 루프 ────────────────────────────────────────────────

    async def _monitor_loop(self) -> None:
        """포트폴리오 스냅샷 + 킬스위치 감시 루프 (매 5분)"""
        repo = PortfolioRepository(self._db)
        while self._running:
            try:
                balances = await self._upbit_ctx.get_balances()
                krw = next((b.available for b in balances if b.currency == "KRW"), 0.0)
                coin_value = sum(
                    b.available * b.avg_buy_price
                    for b in balances if b.currency != "KRW"
                )
                total_equity = krw + coin_value

                await repo.snapshot(
                    total_krw=krw,
                    coin_value=coin_value,
                )

                # 매크로 킬스위치 체크
                await self._coordinator.check_macro(total_equity)

                await asyncio.sleep(300)  # 5분
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("monitor_loop_error", error=str(e))
                await asyncio.sleep(30)

    # ── 주문 실행 ────────────────────────────────────────────────────

    async def _execute_decision(
        self,
        market: str,
        decision: str,
        position_size_pct: float,
        agent_result: dict,
    ) -> None:
        """AI 결정 → 실제 주문 발주"""
        if self._settings.is_paper:
            logger.info(
                "paper_trade_simulated",
                market=market,
                decision=decision,
                position_size_pct=position_size_pct,
            )
            return

        try:
            balances = await self._upbit_ctx.get_balances()
            krw = next((b.available for b in balances if b.currency == "KRW"), 0.0)
            coin_currency = market.split("-")[1]
            coin_balance = next(
                (b for b in balances if b.currency == coin_currency), None
            )

            if decision == "BUY":
                invest_amount = krw * (position_size_pct / 100)
                if invest_amount < 5000:  # 업비트 최소 주문 금액
                    logger.info("order_skipped_min_amount", amount=invest_amount)
                    return

                ticker = await self._upbit_ctx.get_ticker([market])
                current_price = float(ticker[0]["trade_price"]) if ticker else 0
                if current_price <= 0:
                    return

                # 마이크로 킬스위치 확인 (진입 전 호가창 검증)
                orderbooks = await self._upbit_ctx.get_orderbook([market])
                if orderbooks:
                    ob = orderbooks[0]
                    if ob.bid_ask_ratio < 0.5:  # 매도 압력 매우 강함
                        logger.warning("order_blocked_liquidity_wall", market=market)
                        return

                order = await self._upbit_ctx.place_order(
                    market=market,
                    side="bid",
                    price=invest_amount,
                    ord_type="price",  # 시장가 매수
                )
                self._active_orders[order.uuid] = order
                logger.info("order_placed", uuid=order.uuid, market=market, side="bid")

                # 체결 확인 타임아웃 (15초)
                asyncio.create_task(self._monitor_order(order.uuid, ttl=15))

            elif decision == "SELL" and coin_balance and coin_balance.available > 0:
                # 마이크로 킬스위치 확인
                if coin_balance.avg_buy_price > 0:
                    ticker = await self._upbit_ctx.get_ticker([market])
                    if ticker:
                        current_price = float(ticker[0]["trade_price"])
                        await self._coordinator.check_micro(
                            market, coin_balance.avg_buy_price, current_price
                        )

                order = await self._upbit_ctx.place_order(
                    market=market,
                    side="ask",
                    volume=coin_balance.available,
                    ord_type="market",  # 시장가 매도
                )
                self._active_orders[order.uuid] = order
                logger.info("order_placed", uuid=order.uuid, market=market, side="ask")

        except Exception as e:
            logger.error("order_execution_failed", market=market, error=str(e))

    async def _monitor_order(self, uuid: str, ttl: int = 15) -> None:
        """주문 체결 감시 (TTL 초과 시 취소)"""
        await asyncio.sleep(ttl)
        if uuid not in self._active_orders:
            return
        try:
            order = await self._upbit_ctx.get_order(uuid)
            if order.is_pending:
                await self._upbit_ctx.cancel_order(uuid)
                logger.warning("order_cancelled_timeout", uuid=uuid)
            self._active_orders.pop(uuid, None)
        except Exception as e:
            logger.error("order_monitor_failed", uuid=uuid, error=str(e))

    async def panic_sell(self) -> None:
        """긴급 전량 시장가 매도 (/panic_sell 명령)"""
        await self._coordinator.trigger_manual_halt("패닉 셀 실행")
        if self._settings.is_paper:
            logger.info("paper_panic_sell_simulated")
            return

        balances = await self._upbit_ctx.get_balances()
        for balance in balances:
            if balance.currency == "KRW" or balance.available <= 0:
                continue
            market = f"KRW-{balance.currency}"
            try:
                order = await self._upbit_ctx.place_order(
                    market=market,
                    side="ask",
                    volume=balance.available,
                    ord_type="market",
                )
                logger.info("panic_sell_order", market=market, uuid=order.uuid)
            except Exception as e:
                logger.error("panic_sell_failed", market=market, error=str(e))

    # ── 데이터 처리 헬퍼 ─────────────────────────────────────────────

    @staticmethod
    def _candles_to_df(candles) -> pd.DataFrame:
        data = [
            {
                "timestamp": c.timestamp,
                "open": c.open,
                "high": c.high,
                "low": c.low,
                "close": c.close,
                "volume": c.volume,
            }
            for c in candles
        ]
        df = pd.DataFrame(data).set_index("timestamp").sort_index()
        return df

    @staticmethod
    def _compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
        """TA-Lib 기술적 지표 계산"""
        close = df["close"].values.astype(float)
        if len(close) < 50:
            return df

        df = df.copy()
        df["rsi_14"] = talib.RSI(close, timeperiod=14)
        macd, macd_sig, _ = talib.MACD(close, 12, 26, 9)
        df["macd"] = macd
        df["macd_signal"] = macd_sig
        upper, mid, lower = talib.BBANDS(close, timeperiod=20, nbdevup=2, nbdevdn=2)
        df["bb_upper"] = upper
        df["bb_mid"] = mid
        df["bb_lower"] = lower
        df["ema_20"] = talib.EMA(close, timeperiod=20)
        df["ema_50"] = talib.EMA(close, timeperiod=50)
        return df

    def _build_snapshot(self, market: str, df: pd.DataFrame) -> MarketSnapshot:
        """최신 캔들 → MarketSnapshot 변환"""
        last = df.iloc[-1]

        def safe_float(val, default=0.0) -> float:
            try:
                v = float(val)
                return v if not np.isnan(v) else default
            except (TypeError, ValueError):
                return default

        # 24시간 변동률
        if len(df) >= 1440:
            prev_close = df.iloc[-1440]["close"]
        elif len(df) > 1:
            prev_close = df.iloc[0]["close"]
        else:
            prev_close = last["close"]

        change_rate = (last["close"] - prev_close) / prev_close * 100 if prev_close else 0.0

        return {
            "market": market,
            "current_price": safe_float(last["close"]),
            "change_rate_24h": change_rate,
            "volume_24h": safe_float(last["volume"]),
            "rsi_14": safe_float(last.get("rsi_14")),
            "macd": safe_float(last.get("macd")),
            "macd_signal": safe_float(last.get("macd_signal")),
            "bb_upper": safe_float(last.get("bb_upper")),
            "bb_lower": safe_float(last.get("bb_lower")),
            "bb_mid": safe_float(last.get("bb_mid")),
            "ema_20": safe_float(last.get("ema_20")),
            "ema_50": safe_float(last.get("ema_50")),
            "bid_ask_ratio": 1.0,
            "best_bid": 0.0,
            "best_ask": 0.0,
            "total_bid_size": 0.0,
            "total_ask_size": 0.0,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    @staticmethod
    def _build_initial_state(snapshot: MarketSnapshot) -> AgentState:
        return {
            "market_data": snapshot,
            "portfolio_krw": 0.0,
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


async def main() -> None:
    from src.config.logging_config import configure_logging
    from src.config.settings import get_settings

    settings = get_settings()
    configure_logging(settings.log_level)

    trader = Trader(settings)
    await trader.run()


if __name__ == "__main__":
    asyncio.run(main())
