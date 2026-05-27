"""텔레그램 명령 핸들러"""
import structlog
from datetime import datetime, timezone
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from src.config.settings import Settings

logger = structlog.get_logger(__name__)


class CommandHandlers:
    def __init__(
        self,
        settings: Settings,
        db=None,
        coordinator=None,
        trader=None,
        strategy_manager=None,
    ) -> None:
        self._settings = settings
        self._db = db
        self._coordinator = coordinator
        self._trader = trader
        self._strategy_manager = strategy_manager

    def _help_text(self) -> str:
        mode_emoji = "📄" if self._settings.is_paper else "💰"
        mode_text = "모의 매매" if self._settings.is_paper else "실거래"
        return (
            f"🤖 <b>코인 자동매매 봇</b>\n\n"
            f"{mode_emoji} 현재 모드: <b>{mode_text}</b>\n\n"
            f"<b>조회</b>\n"
            f"/status - 현재 상태 조회\n"
            f"/settings - 현재 설정값 조회\n"
            f"/trades [개수] - 매매 기록 조회 (기본 10건)\n"
            f"/logs [개수] - 최근 로그 조회\n"
            f"/pyramid_status - 피라미딩 포지션 상태 조회\n\n"
            f"<b>제어</b>\n"
            f"/halt - 매매 중단 (킬스위치)\n"
            f"/resume [마켓] - 매매 재개 (마켓 지정 시 해당 마켓 킬스위치만 해제)\n"
            f"/strategy [이름] - 전략 변경\n"
            f"/backtest [마켓] [일수] [param=val ...] - 백테스트 실행\n\n"
            f"<b>피라미딩 동기화</b>\n"
            f"/sync - 업비트 잔고 기준 상태 동기화\n"
            f"/pyramid_set [마켓] [진입가] [횟수] - 상태 수동 설정\n\n"
            f"<b>매수 제외</b>\n"
            f"/block [마켓] [사유] - 특정 종목 매수 금지\n"
            f"/unblock [마켓] - 매수 금지 해제\n"
            f"/blocked - 매수 금지 목록 조회\n\n"
            f"<b>수동 매도</b>\n"
            f"/sell [마켓] - 특정 종목 즉시 시장가 매도 (생략 시 목록 표시)\n\n"
            f"<b>긴급</b>\n"
            f"/panic_sell - 전량 시장가 매도\n\n"
            f"<b>설정</b>\n"
            f"/set_param [파라미터] [값] - 피라미딩 파라미터 즉시 변경\n"
            f"  trail_pct / stop_pct / add_pct / entry_pct / unit_amount\n"
            f"/reload_settings - .env 설정값 런타임 재로드"
        )

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text(self._help_text(), parse_mode="HTML")

    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text(self._help_text(), parse_mode="HTML")

    async def _build_status_text(self) -> tuple[str, InlineKeyboardMarkup]:
        """상태 텍스트와 키보드 생성 (cmd_status, refresh_status 공유)"""
        ks_status = self._coordinator.status if self._coordinator else {}
        halted_text = "🔴 중단됨" if ks_status.get("is_halted") else "🟢 운영 중"

        strategy_name = (
            self._strategy_manager.active_strategy_name
            if self._strategy_manager else "없음"
        )

        portfolio_text = "데이터 없음"
        if self._db:
            from src.persistence.repositories.portfolio import PortfolioRepository
            repo = PortfolioRepository(self._db)
            curve = await repo.get_equity_curve(hours=1)
            if curve:
                latest = curve[-1]
                equity = latest.get("equity", 0)
                portfolio_text = f"{equity:,.0f}원"

        now = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
        text = (
            f"📊 <b>시스템 상태</b>\n\n"
            f"매매 상태: {halted_text}\n"
            f"현재 전략: <code>{strategy_name or '없음'}</code>\n"
            f"총 자산: {portfolio_text}\n"
            f"운영 모드: {'모의' if self._settings.is_paper else '실거래'}\n\n"
            f"차단된 마켓: {', '.join(ks_status.get('blocked_markets', [])) or '없음'}\n\n"
            f"<i>갱신: {now}</i>"
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 새로고침", callback_data="refresh_status")],
        ])
        return text, keyboard

    async def cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """현재 상태 요약 보고"""
        try:
            text, keyboard = await self._build_status_text()
            await update.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)
        except Exception as e:
            await update.message.reply_text(f"❌ 상태 조회 실패: {e}")

    async def cmd_halt(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """매크로 킬 스위치 수동 발동"""
        if not self._coordinator:
            await update.message.reply_text("❌ 코디네이터가 초기화되지 않았습니다")
            return

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ 확인 - 매매 중단", callback_data="confirm_halt"),
                InlineKeyboardButton("❌ 취소", callback_data="cancel"),
            ]
        ])
        await update.message.reply_text(
            "⚠️ <b>매매를 중단하시겠습니까?</b>\n\n"
            "킬 스위치를 발동하면 모든 새 주문이 차단됩니다.",
            parse_mode="HTML",
            reply_markup=keyboard,
        )

    async def cmd_resume(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """킬 스위치 해제. /resume [마켓] 으로 특정 마켓만 해제 가능."""
        if not self._coordinator:
            await update.message.reply_text("❌ 코디네이터가 초기화되지 않았습니다")
            return
        args = context.args or []
        if args:
            market = args[0].upper()
            try:
                success = await self._coordinator.reset_market(market)
                if success:
                    await update.message.reply_text(
                        f"✅ <b>{market}</b> 킬스위치 해제 완료.",
                        parse_mode="HTML",
                    )
                else:
                    await update.message.reply_text(
                        f"ℹ️ {market}은 킬스위치 차단 상태가 아닙니다."
                    )
            except Exception as e:
                await update.message.reply_text(f"❌ 해제 실패: {e}")
        else:
            try:
                await self._coordinator.reset(confirm=True)
                await update.message.reply_text("✅ 킬 스위치 전체 해제 완료. 매매를 재개합니다.")
            except Exception as e:
                await update.message.reply_text(f"❌ 해제 실패: {e}")

    async def cmd_strategy(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """전략 변경"""
        if not context.args:
            available = (
                self._strategy_manager.list_available()
                if self._strategy_manager else []
            )
            await update.message.reply_text(
                f"사용법: /strategy [전략명]\n\n"
                f"사용 가능한 전략: {', '.join(available) or '없음'}"
            )
            return

        strategy_name = context.args[0]
        try:
            params = (
                {"unit_amount": self._settings.pyramid_unit_amount}
                if "pyramid" in strategy_name else None
            )
            self._strategy_manager.activate(strategy_name, params=params)
            await update.message.reply_text(
                f"✅ 전략이 <code>{strategy_name}</code>으로 변경되었습니다.\n"
                f"서버 재시작 없이 즉시 적용됩니다.",
                parse_mode="HTML",
            )
        except FileNotFoundError:
            await update.message.reply_text(f"❌ '{strategy_name}' 전략 파일을 찾을 수 없습니다.")
        except Exception as e:
            await update.message.reply_text(f"❌ 전략 변경 실패: {e}")

    async def cmd_backtest(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """백테스트 실행
        사용법: /backtest [마켓] [일수] [param=value ...]
        예) /backtest KRW-BTC 30
            /backtest KRW-BTC 14 trail_pct=8 stop_pct=5 capital=2000000
        """
        import asyncio
        import pandas as pd
        from pathlib import Path
        from src.backtest.engine import BacktestEngine
        from src.backtest.fees import FeeSchedule
        from src.backtest.slippage import ConservativeSlippage
        from src.strategy.manager import StrategyManager

        args = context.args or []

        # 인수 없으면 파라미터 안내 출력
        if not args:
            strategy = self._strategy_manager.get_active() if self._strategy_manager else None
            if strategy:
                # 전략 인스턴스의 실제 속성을 동적으로 나열
                skip = {"name"}
                param_lines = "\n".join(
                    f"  <code>{k}</code> = {v}"
                    for k, v in vars(strategy).items()
                    if not k.startswith("_") and k not in skip
                )
                current = f"\n현재 전략: <code>{strategy.name}</code>\n{param_lines}"
            else:
                current = "\n⚠️ 활성화된 전략 없음"

            await update.message.reply_text(
                f"📋 <b>백테스트 사용법</b>\n\n"
                f"<code>/backtest [마켓] [일수] [파라미터=값 ...]</code>\n\n"
                f"<b>공통 파라미터:</b>\n"
                f"  <code>capital</code>     — 초기 자본금 원 (기본: unit_amount×10)\n\n"
                f"<b>전략별 파라미터 (전략 파라미터는 오버라이드 가능):</b>\n"
                f"  <b>pyramid_breakout</b>: entry_pct / add_pct / stop_pct / trail_pct / unit_amount\n"
                f"  <b>breakout_n</b>:       window / stop_pct / profit_pct / trail_pct / unit_amount\n"
                f"  <b>ma_cross</b>:         fast_period / slow_period / stop_pct / profit_pct / trail_pct / ma_exit / unit_amount\n\n"
                f"<b>캔들 단위 자동 선택:</b>\n"
                f"  7~30일 → 60분봉 | 31~90일 → 4시간봉 | 91~365일 → 일봉\n\n"
                f"<b>예시:</b>\n"
                f"  <code>/backtest KRW-BTC 365</code>\n"
                f"  <code>/backtest KRW-ETH 90 stop_pct=3 trail_pct=3</code>\n"
                f"  <code>/backtest KRW-BTC 180 window=10 profit_pct=5 capital=1000000</code>"
                f"{current}",
                parse_mode="HTML",
            )
            return

        # 인수 파싱: [마켓] [일수] [key=val ...]
        market = "KRW-BTC"
        days = 30
        idx = 0

        if args and args[0].upper().startswith("KRW-"):
            market = args[0].upper()
            idx = 1

        if idx < len(args):
            try:
                days = int(args[idx])
                idx += 1
            except ValueError:
                pass

        # key=value 및 key value 두 형식 모두 지원
        overrides: dict = {}
        remaining = args[idx:]
        i = 0
        while i < len(remaining):
            token = remaining[i]
            if "=" in token:
                key, _, val_str = token.partition("=")
                try:
                    overrides[key.strip()] = float(val_str.strip())
                except ValueError:
                    overrides[key.strip()] = val_str.strip()
                i += 1
            elif i + 1 < len(remaining):
                key = token
                try:
                    overrides[key] = float(remaining[i + 1])
                    i += 2
                except ValueError:
                    i += 1
            else:
                i += 1

        raw_days = days
        days = max(7, min(days, 365))
        days_capped = raw_days > 365
        capital = float(overrides.pop("capital", self._settings.pyramid_unit_amount * 10))

        # 기간에 따라 캔들 단위 자동 선택 (API 호출 수 최소화)
        if days <= 30:
            candle_unit = 60    # 60분봉
            candle_label = "60분봉"
            target_count = days * 24
            use_daily = False
        elif days <= 90:
            candle_unit = 240   # 4시간봉
            candle_label = "4시간봉"
            target_count = days * 6
            use_daily = False
        else:
            candle_label = "일봉"
            target_count = days
            use_daily = True

        if not self._strategy_manager:
            await update.message.reply_text("❌ 전략 매니저 미연결")
            return

        strategy = self._strategy_manager.get_active()
        if not strategy:
            await update.message.reply_text(
                "❌ 활성화된 전략이 없습니다.\n/strategy [이름]으로 먼저 전략을 선택하세요."
            )
            return

        progress_msg = await update.message.reply_text(
            f"⏳ <b>{strategy.name}</b> | <code>{market}</code> | {days}일 ({candle_label})\n"
            f"캔들 데이터 수집 중...",
            parse_mode="HTML",
        )

        try:
            from datetime import timedelta
            from src.exchange.upbit_client import UpbitClient

            # 실시간 매매 루프의 rate limit 버킷과 경합하지 않도록 전용 클라이언트 사용
            async with UpbitClient(self._settings) as client:
                all_candles = []
                to_param = None

                while len(all_candles) < target_count:
                    if use_daily:
                        batch = await client.get_candles_days(market, count=200, to=to_param)
                    else:
                        batch = await client.get_candles_minutes(
                            market, unit=candle_unit, count=200, to=to_param
                        )
                    if not batch:
                        break
                    all_candles.extend(batch)
                    # 마지막 캔들 시각에서 1단위 이전으로 to_param 설정 (중복 방지)
                    last_ts = batch[-1].timestamp
                    step = timedelta(days=1) if use_daily else timedelta(minutes=candle_unit)
                    to_param = (last_ts - step).strftime("%Y-%m-%dT%H:%M:%S")
                    await asyncio.sleep(0.2)  # Quotation API: 9 rps, 여유 있게 설정

            if not all_candles:
                await progress_msg.edit_text(f"❌ {market} 캔들 데이터를 가져오지 못했습니다.")
                return

            data = [
                {
                    "timestamp": c.timestamp,
                    "open": c.open, "high": c.high, "low": c.low,
                    "close": c.close, "volume": c.volume,
                }
                for c in all_candles
            ]
            df = pd.DataFrame(data).set_index("timestamp").sort_index()
            df.index = pd.to_datetime(df.index, utc=True)
            df = df[~df.index.duplicated(keep="first")]

            await progress_msg.edit_text(
                f"⏳ <b>{strategy.name}</b> | <code>{market}</code> | {days}일 ({candle_label})\n"
                f"백테스트 실행 중 ({len(df):,}개 캔들)...",
                parse_mode="HTML",
            )

            strategy_params = {
                "unit_amount": getattr(strategy, "unit_amount", self._settings.pyramid_unit_amount),
                "entry_pct":   getattr(strategy, "entry_pct",   self._settings.pyramid_entry_pct),
                "add_pct":     getattr(strategy, "add_pct",     self._settings.pyramid_add_pct),
                "stop_pct":    getattr(strategy, "stop_pct",    self._settings.pyramid_stop_pct),
                "trail_pct":   getattr(strategy, "trail_pct",   self._settings.pyramid_trail_pct),
            }
            strategy_params.update(overrides)

            temp_manager = StrategyManager(Path("src/strategy"))
            bt_strategy = temp_manager.load(strategy.name, params=strategy_params)

            engine = BacktestEngine(
                strategy=bt_strategy,
                slippage=ConservativeSlippage(),
                fee=FeeSchedule(rate_bps=self._settings.default_fee_bps),
            )
            result = engine.run(df, initial_capital=capital)

            s = result.report.summary()
            ret_pct       = s.get("total_return_pct", 0)
            mdd           = s.get("max_drawdown_pct", 0)
            sharpe        = s.get("sharpe_ratio", 0)
            profit_factor = s.get("profit_factor", 0)
            win_rate      = s.get("win_rate_pct", 0)
            total_trades  = s.get("total_trades", 0)
            avg_pnl       = s.get("avg_pnl_per_trade", 0)
            final_cap     = s.get("final_capital", capital)

            ret_emoji = "📈" if ret_pct >= 0 else "📉"
            override_text = ""
            if overrides:
                override_text = "\n파라미터: " + " / ".join(
                    f"<code>{k}={v:g}</code>" for k, v in overrides.items()
                    if isinstance(v, (int, float))
                )
            cap_note = f"\n<i>⚠️ {raw_days}일 요청 → 최대 365일로 제한됨</i>" if days_capped else ""

            await progress_msg.edit_text(
                f"📊 <b>백테스트 결과</b>\n\n"
                f"전략: <code>{strategy.name}</code>\n"
                f"마켓: <code>{market}</code>\n"
                f"기간: {df.index[0].date()} ~ {df.index[-1].date()} ({candle_label}){override_text}{cap_note}\n\n"
                f"{ret_emoji} 총 수익률: <b>{ret_pct:+.2f}%</b>\n"
                f"💰 최종 자본: {final_cap:,.0f}원 (초기 {capital:,.0f}원)\n"
                f"📉 최대 낙폭(MDD): {mdd:.2f}%\n"
                f"📐 샤프 비율: {sharpe:.2f}\n"
                f"⚡ 프로핏 팩터: {profit_factor:.2f}\n\n"
                f"🎯 승률: {win_rate:.1f}%\n"
                f"📋 총 거래: {total_trades}회\n"
                f"💵 평균 손익/거래: {avg_pnl:+,.0f}원",
                parse_mode="HTML",
            )

        except Exception as e:
            logger.error("backtest_failed", error=str(e))
            await progress_msg.edit_text(f"❌ 백테스트 실패: {e}")

    async def cmd_logs(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """최근 로그 조회"""
        limit = int(context.args[0]) if context.args else 10
        if not self._db:
            await update.message.reply_text("❌ 데이터베이스 미연결")
            return

        from src.persistence.repositories.logs import LogRepository
        repo = LogRepository(self._db)
        logs = await repo.get_recent(limit=limit)

        if not logs:
            await update.message.reply_text("로그가 없습니다.")
            return

        lines = [f"📋 최근 {limit}개 로그\n"]
        for log in logs[:10]:  # 텔레그램 메시지 길이 제한
            level_emoji = {"ERROR": "❌", "WARNING": "⚠️", "INFO": "ℹ️"}.get(
                log.get("level", ""), "•"
            )
            lines.append(
                f"{level_emoji} [{log.get('level','')}] {log.get('message', '')[:80]}"
            )

        await update.message.reply_text("\n".join(lines), parse_mode="HTML")

    async def cmd_trades(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """최근 매매 기록 조회"""
        limit = int(context.args[0]) if context.args else 10
        if not self._db:
            await update.message.reply_text("❌ 데이터베이스 미연결")
            return

        from src.persistence.repositories.trades import TradeRepository
        repo = TradeRepository(self._db)
        trades = await repo.get_recent(limit=limit)
        summary = await repo.get_performance_summary(days=30)

        if not trades:
            await update.message.reply_text("📭 매매 기록이 없습니다.")
            return

        # 성과 요약
        total = summary.get("total_trades", 0)
        wins = summary.get("wins", 0)
        total_pnl = summary.get("total_pnl") or 0.0
        win_rate = wins / total * 100 if total > 0 else 0.0
        pnl_emoji = "📈" if total_pnl >= 0 else "📉"

        lines = [
            f"📊 <b>매매 기록 (최근 {limit}건)</b>\n",
            f"─ 30일 성과 ─",
            f"총 거래: {total}건 | 승률: {win_rate:.1f}%",
            f"{pnl_emoji} 누적 손익: {total_pnl:+,.0f}원\n",
            f"─ 최근 거래 ─",
        ]

        for t in trades:
            status = t.get("status", "")
            if status == "open":
                status_mark = "🔵 보유중"
            elif t.get("pnl") is not None and t["pnl"] >= 0:
                status_mark = "📈 익절"
            else:
                status_mark = "📉 손절" if status == "closed" else "⚪"

            pnl_text = f"{t['pnl']:+,.0f}원" if t.get("pnl") is not None else "-"
            side_text = "매수" if t.get("side") == "bid" else "매도"
            lines.append(
                f"{status_mark} {t['market']} {side_text} | "
                f"{t['price']:,.0f}원 | PnL: {pnl_text}"
            )

        await update.message.reply_text("\n".join(lines), parse_mode="HTML")

    async def cmd_settings(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """현재 설정값 조회"""
        s = self._settings
        markets = ", ".join(s.markets_list) if s.target_markets_top_n == 0 else f"거래대금 상위 {s.target_markets_top_n}개 자동선택"
        mode_emoji = "📄" if s.is_paper else "💰"

        excluded_static = ", ".join(s.excluded_markets_list) or "없음"
        text = (
            f"⚙️ <b>현재 설정</b>\n\n"
            f"<b>매매</b>\n"
            f"  모드: {mode_emoji} {'모의 매매' if s.is_paper else '실거래'}\n"
            f"  전략: <code>{s.default_strategy}</code>\n"
            f"  대상 마켓: {markets}\n"
            f"  매수 제외(설정): {excluded_static}\n"
            f"  매매 주기: {s.trade_interval_seconds}초\n"
            f"  캔들(진입): {'일봉' if s.candle_unit_minutes == 0 else f'{s.candle_unit_minutes}분봉'} × {s.candle_count}개\n"
            f"  캔들(보유중): {'일봉' if s.candle_unit_position_minutes == 0 else f'{s.candle_unit_position_minutes}분봉'} × {s.candle_count}개\n\n"
            f"<b>리스크 관리</b>\n"
            f"  최대 낙폭 (매크로 킬스위치): {s.macro_max_drawdown_pct}%\n"
            f"  손절 (마이크로 킬스위치): {s.micro_stop_loss_pct}%\n"
            f"  단일 코인 최대 비중: {s.max_position_pct}%\n\n"
            f"<b>피라미딩 전략</b>\n"
            f"  1회 투입 금액: {s.pyramid_unit_amount:,.0f}원\n"
            f"  진입: 저점 대비 +{s.pyramid_entry_pct}%\n"
            f"  추가매수: 진입가 대비 +{s.pyramid_add_pct}% 간격\n"
            f"  손절: 진입가 대비 -{s.pyramid_stop_pct}%\n"
            f"  트레일링 스탑: 최고가 대비 -{s.pyramid_trail_pct}%\n"
            f"  재진입 쿨다운: 매도 후 {s.pyramid_sell_cooldown_minutes}분\n\n"
            f"<b>알림</b>\n"
            f"  원화 잔고 경고 기준: {s.min_krw_alert:,.0f}원\n\n"
            f"<b>수수료/슬리피지</b>\n"
            f"  수수료: {s.default_fee_bps}bps ({s.default_fee_bps/100:.2f}%)\n"
            f"  슬리피지: {s.default_slippage_bps}bps ({s.default_slippage_bps/100:.2f}%)"
        )
        await update.message.reply_text(text, parse_mode="HTML")

    async def cmd_sync(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """업비트 잔고 기준 피라미딩 상태 동기화"""
        if not self._trader:
            await update.message.reply_text("❌ 트레이더 미연결")
            return
        try:
            result = await self._trader.sync_pyramid_state()
            removed = result["removed"]
            added = result["added"]

            lines = ["🔄 <b>피라미딩 상태 동기화 완료</b>\n"]
            if removed:
                lines.append(f"🗑 제거 (잔고 없음): {', '.join(removed)}")
            if added:
                lines.append(f"➕ 추가 (평균단가 기준): {', '.join(added)}")
            if not removed and not added:
                lines.append("✅ 변경 없음 — 이미 동기화된 상태입니다.")

            await update.message.reply_text("\n".join(lines), parse_mode="HTML")
        except Exception as e:
            await update.message.reply_text(f"❌ 동기화 실패: {e}")

    async def cmd_pyramid_set(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """피라미딩 상태 수동 설정
        사용법: /pyramid_set KRW-BTC 95000000 2
        """
        if not self._trader:
            await update.message.reply_text("❌ 트레이더 미연결")
            return
        args = context.args or []
        if len(args) != 3:
            await update.message.reply_text(
                "사용법: <code>/pyramid_set [마켓] [진입가] [추가매수횟수]</code>\n\n"
                "예) <code>/pyramid_set KRW-BTC 95000000 2</code>\n"
                "→ BTC 진입가 9,500만원, 추가매수 2회 완료로 기록",
                parse_mode="HTML",
            )
            return
        try:
            market = args[0].upper()
            entry_price = float(args[1].replace(",", ""))
            add_count = int(args[2])
            if entry_price <= 0 or add_count < 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text("❌ 입력값 오류: 진입가는 양수, 추가매수 횟수는 0 이상 정수")
            return

        await self._trader.set_pyramid_state(market, entry_price, add_count)

        strategy = self._trader._strategy_manager.get_active() if self._trader._strategy_manager else None
        add_pct = getattr(strategy, "add_pct", 10.0) if strategy else 10.0
        next_level = entry_price * (1 + add_pct / 100 * (add_count + 1))

        await update.message.reply_text(
            f"✅ <b>피라미딩 상태 설정 완료</b>\n\n"
            f"마켓: <code>{market}</code>\n"
            f"진입가: {entry_price:,.0f}원\n"
            f"추가매수 완료: {add_count}회\n"
            f"다음 추가매수 기준: {next_level:,.0f}원 ({add_pct}% 상승)",
            parse_mode="HTML",
        )

    async def cmd_pyramid_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """현재 피라미딩 상태 조회"""
        if not self._trader:
            await update.message.reply_text("❌ 트레이더 미연결")
            return

        state = self._trader._pyramid_state
        if not state:
            await update.message.reply_text("📭 현재 추적 중인 피라미딩 포지션이 없습니다.")
            return

        strategy = self._trader._strategy_manager.get_active() if self._trader._strategy_manager else None
        add_pct = getattr(strategy, "add_pct", 10.0) if strategy else 10.0

        lines = ["📊 <b>피라미딩 포지션 상태</b>\n"]
        for market, s in state.items():
            entry = s["entry_price"]
            count = s["add_count"]
            next_level = entry * (1 + add_pct / 100 * (count + 1))
            lines.append(
                f"<code>{market}</code>\n"
                f"  진입가: {entry:,.0f}원\n"
                f"  추가매수: {count}회 완료\n"
                f"  다음 기준: {next_level:,.0f}원\n"
            )
        await update.message.reply_text("\n".join(lines), parse_mode="HTML")

    async def cmd_block(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """특정 마켓 매수 금지 등록. 사용법: /block KRW-BTC [사유]"""
        if not self._trader:
            await update.message.reply_text("❌ 트레이더 미연결")
            return
        args = context.args or []
        if not args:
            await update.message.reply_text(
                "사용법: <code>/block [마켓] [사유(선택)]</code>\n"
                "예) <code>/block KRW-BTC 고점 판단</code>",
                parse_mode="HTML",
            )
            return
        market = args[0].upper()
        reason = " ".join(args[1:]) if len(args) > 1 else ""
        await self._trader.block_market(market, reason)
        await update.message.reply_text(
            f"🚫 <b>{market}</b> 매수 금지 등록 완료\n"
            + (f"사유: {reason}" if reason else "사유: 없음"),
            parse_mode="HTML",
        )

    async def cmd_unblock(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """매수 금지 해제. 사용법: /unblock KRW-BTC"""
        if not self._trader:
            await update.message.reply_text("❌ 트레이더 미연결")
            return
        args = context.args or []
        if not args:
            await update.message.reply_text(
                "사용법: <code>/unblock [마켓]</code>\n"
                "예) <code>/unblock KRW-BTC</code>",
                parse_mode="HTML",
            )
            return
        market = args[0].upper()
        removed = await self._trader.unblock_market(market)
        if removed:
            await update.message.reply_text(f"✅ <b>{market}</b> 매수 금지 해제 완료", parse_mode="HTML")
        else:
            await update.message.reply_text(f"ℹ️ {market}은 매수 금지 목록에 없습니다.")

    async def cmd_blocked(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """현재 매수 금지 목록 조회"""
        if not self._trader:
            await update.message.reply_text("❌ 트레이더 미연결")
            return
        excluded = self._trader._excluded_markets
        if not excluded:
            await update.message.reply_text("📭 매수 금지된 마켓이 없습니다.")
            return
        lines = ["🚫 <b>매수 금지 목록</b>\n"]
        for market, reason in sorted(excluded.items()):
            lines.append(f"• <code>{market}</code>" + (f" — {reason}" if reason else ""))
        await update.message.reply_text("\n".join(lines), parse_mode="HTML")

    async def cmd_sell(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """보유 종목 선택 매도. /sell 또는 /sell KRW-BTC"""
        if not self._trader:
            await update.message.reply_text("❌ 트레이더 미연결")
            return
        args = context.args or []
        if args:
            market = args[0].upper()
            if market not in self._trader._held_markets:
                await update.message.reply_text(f"❌ <code>{market}</code> 보유 중이지 않습니다.", parse_mode="HTML")
                return
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ 매도 실행", callback_data=f"sell_confirm:{market}"),
                InlineKeyboardButton("❌ 취소", callback_data="cancel"),
            ]])
            await update.message.reply_text(
                f"🔴 <b>{market} 매도 확인</b>\n\n시장가로 전량 매도하시겠습니까?",
                parse_mode="HTML", reply_markup=keyboard,
            )
        else:
            held = sorted(self._trader._held_markets)
            if not held:
                await update.message.reply_text("📭 현재 보유 중인 종목이 없습니다.")
                return
            buttons = [[InlineKeyboardButton(m, callback_data=f"sell_pick:{m}")] for m in held]
            buttons.append([InlineKeyboardButton("❌ 취소", callback_data="cancel")])
            await update.message.reply_text(
                "📊 <b>매도할 종목을 선택하세요</b>",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(buttons),
            )

    async def cmd_set_param(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """전략 파라미터 즉시 변경 (재시작 전까지 유효).
        사용법: /set_param [파라미터] [값]
        """
        if not self._strategy_manager:
            await update.message.reply_text("❌ 전략 매니저 미연결")
            return

        strategy = self._strategy_manager.get_active()
        if not strategy:
            await update.message.reply_text("❌ 활성화된 전략이 없습니다.")
            return

        # 현재 전략의 파라미터 목록을 동적으로 파악
        skip = {"name"}
        current = {
            k: v for k, v in vars(strategy).items()
            if not k.startswith("_") and k not in skip
        }

        args = context.args or []
        if len(args) != 2:
            param_list = "\n".join(
                f"  <code>{k}</code> = {v}" for k, v in current.items()
            )
            await update.message.reply_text(
                f"사용법: <code>/set_param [파라미터] [값]</code>\n\n"
                f"현재 전략 <b>{strategy.name}</b> 파라미터:\n"
                f"{param_list}\n\n"
                f"예) <code>/set_param trail_pct 3</code>",
                parse_mode="HTML",
            )
            return

        param = args[0].lower()
        if param not in current:
            await update.message.reply_text(
                f"❌ 알 수 없는 파라미터: <code>{param}</code>\n"
                f"변경 가능: {', '.join(sorted(current.keys()))}",
                parse_mode="HTML",
            )
            return

        try:
            value = float(args[1].replace(",", ""))
        except ValueError:
            await update.message.reply_text("❌ 값이 올바르지 않습니다. 숫자를 입력하세요.")
            return

        old_value = current[param]
        current[param] = value

        try:
            self._strategy_manager.activate(strategy.name, params=current)
        except Exception as e:
            await update.message.reply_text(f"❌ 파라미터 변경 실패: {e}")
            return

        unit = "원" if param == "unit_amount" else ""
        await update.message.reply_text(
            f"✅ <b>{strategy.name} / {param} 변경 완료</b>\n\n"
            f"{old_value:g}{unit} → <b>{value:g}{unit}</b>\n\n"
            f"<i>⚠️ 재시작 시 초기값으로 돌아갑니다.</i>",
            parse_mode="HTML",
        )

    async def cmd_reload_settings(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """.env 설정값을 런타임에 다시 로드한다."""
        if not self._trader:
            await update.message.reply_text("❌ 트레이더 미연결")
            return
        try:
            result = await self._trader.reload_settings()
            self._settings = self._trader._settings  # 핸들러 설정 레퍼런스 동기화
            changes = result.get("changes", [])
            if changes:
                body = "\n".join(f"• {c}" for c in changes)
                await update.message.reply_text(
                    f"✅ <b>설정 재로드 완료</b>\n\n{body}",
                    parse_mode="HTML",
                )
            else:
                await update.message.reply_text("✅ 설정 재로드 완료. 변경된 값이 없습니다.")
        except Exception as e:
            await update.message.reply_text(f"❌ 설정 재로드 실패: {e}")

    async def cmd_panic_sell(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """긴급 전량 시장가 매도"""
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🚨 전량 매도 실행", callback_data="confirm_panic_sell"),
                InlineKeyboardButton("❌ 취소", callback_data="cancel"),
            ]
        ])
        await update.message.reply_text(
            "🚨 <b>긴급 전량 매도</b>\n\n"
            "⚠️ 모든 보유 코인을 즉시 시장가로 매도합니다.\n"
            "이 작업은 되돌릴 수 없습니다!",
            parse_mode="HTML",
            reply_markup=keyboard,
        )

    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """인라인 버튼 콜백 처리"""
        query = update.callback_query
        await query.answer()

        data = query.data
        if data == "confirm_halt":
            if self._coordinator:
                await self._coordinator.trigger_manual_halt("텔레그램 관리자 명령")
            await query.edit_message_text("🔴 킬 스위치 발동 완료. 모든 매매가 차단되었습니다.")

        elif data == "confirm_panic_sell":
            # 트레이더에 패닉 셀 위임
            if self._trader:
                try:
                    await self._trader.panic_sell()
                    await query.edit_message_text("✅ 전량 매도 완료.")
                except Exception as e:
                    await query.edit_message_text(f"❌ 매도 실패: {e}")
            else:
                await query.edit_message_text("❌ 트레이더 미연결")

        elif data == "refresh_status":
            try:
                text, keyboard = await self._build_status_text()
                await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
            except Exception as e:
                await query.edit_message_text(f"❌ 상태 조회 실패: {e}")

        elif data.startswith("sell_pick:"):
            market = data.split(":", 1)[1]
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ 매도 실행", callback_data=f"sell_confirm:{market}"),
                InlineKeyboardButton("❌ 취소", callback_data="cancel"),
            ]])
            await query.edit_message_text(
                f"🔴 <b>{market} 매도 확인</b>\n\n시장가로 전량 매도하시겠습니까?",
                parse_mode="HTML", reply_markup=keyboard,
            )

        elif data.startswith("sell_confirm:"):
            market = data.split(":", 1)[1]
            if self._trader:
                try:
                    success = await self._trader.sell_market(market)
                    if success:
                        await query.edit_message_text(f"✅ <b>{market}</b> 매도 주문 전송 완료.", parse_mode="HTML")
                    else:
                        await query.edit_message_text(f"❌ {market} 보유 잔고가 없습니다.")
                except Exception as e:
                    await query.edit_message_text(f"❌ 매도 실패: {e}")
            else:
                await query.edit_message_text("❌ 트레이더 미연결")

        elif data == "cancel":
            await query.edit_message_text("취소되었습니다.")
