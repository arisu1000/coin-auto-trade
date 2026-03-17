"""텔레그램 명령 핸들러"""
import structlog
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

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        mode_emoji = "📄" if self._settings.is_paper else "💰"
        mode_text = "모의 매매" if self._settings.is_paper else "실거래"
        await update.message.reply_text(
            f"🤖 <b>코인 자동매매 봇</b>\n\n"
            f"{mode_emoji} 현재 모드: <b>{mode_text}</b>\n\n"
            f"사용 가능한 명령어:\n"
            f"/status - 현재 상태 조회\n"
            f"/halt - 매매 중단 (킬스위치)\n"
            f"/resume - 매매 재개\n"
            f"/strategy [이름] - 전략 변경\n"
            f"/backtest [전략] [일수] - 백테스트 실행\n"
            f"/logs [개수] - 최근 로그 조회\n"
            f"/panic_sell - 긴급 전량 매도",
            parse_mode="HTML",
        )

    async def cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """현재 상태 요약 보고"""
        try:
            # 킬 스위치 상태
            ks_status = self._coordinator.status if self._coordinator else {}
            halted_text = "🔴 중단됨" if ks_status.get("is_halted") else "🟢 운영 중"

            # 전략 정보
            strategy_name = (
                self._strategy_manager.active_strategy_name
                if self._strategy_manager else "없음"
            )

            # 포트폴리오 (DB에서)
            portfolio_text = "데이터 없음"
            if self._db:
                from src.persistence.repositories.portfolio import PortfolioRepository
                repo = PortfolioRepository(self._db)
                curve = await repo.get_equity_curve(hours=1)
                if curve:
                    latest = curve[-1]
                    equity = latest.get("equity", 0)
                    portfolio_text = f"{equity:,.0f}원"

            text = (
                f"📊 <b>시스템 상태</b>\n\n"
                f"매매 상태: {halted_text}\n"
                f"현재 전략: <code>{strategy_manager_name}</code>\n"
                f"총 자산: {portfolio_text}\n"
                f"운영 모드: {'모의' if self._settings.is_paper else '실거래'}\n\n"
                f"차단된 마켓: {', '.join(ks_status.get('blocked_markets', [])) or '없음'}"
            )
            # 변수명 수정
            text = text.replace("strategy_manager_name", strategy_name or "없음")

            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 새로고침", callback_data="refresh_status")],
            ])
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
        """킬 스위치 해제"""
        if not self._coordinator:
            await update.message.reply_text("❌ 코디네이터가 초기화되지 않았습니다")
            return
        try:
            await self._coordinator.reset(confirm=True)
            await update.message.reply_text("✅ 킬 스위치 해제 완료. 매매를 재개합니다.")
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
            self._strategy_manager.activate(strategy_name)
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
            await query.edit_message_text("🔄 상태 갱신 중...")
            await self.cmd_status(update, context)

        elif data == "cancel":
            await query.edit_message_text("취소되었습니다.")
