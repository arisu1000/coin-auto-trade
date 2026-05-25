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
            f"/backtest [전략] [일수] - 백테스트 실행\n\n"
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
        """백테스트 실행"""
        args = context.args or []
        strategy_name = args[0] if args else self._settings.default_strategy
        days = int(args[1]) if len(args) > 1 else 30

        await update.message.reply_text(
            f"⏳ <b>{strategy_name}</b> 전략으로 {days}일 백테스트 실행 중...",
            parse_mode="HTML",
        )
        # 실제 백테스트는 별도 태스크로 실행
        await update.message.reply_text("🚧 백테스트 기능 준비 중입니다.")

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
