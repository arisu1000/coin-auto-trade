"""
텔레그램 봇 - 비동기 원격 제어 인터페이스

python-telegram-bot v21 (asyncio 네이티브)을 사용하여
트레이딩 메인 루프와 동일한 이벤트 루프에서 실행된다.
교착 상태 없음, 타임아웃 없음.
"""
import structlog
from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from src.bot.handlers import CommandHandlers
from src.config.settings import Settings

logger = structlog.get_logger(__name__)


class TradingBot:
    """
    텔레그램 봇 라이프사이클 관리자

    사용법:
        bot = TradingBot(settings, db, coordinator, trader)
        # asyncio.gather()로 트레이딩 루프와 함께 실행
        await asyncio.gather(trader.run(), bot.start())
    """

    def __init__(self, settings: Settings, dependencies: dict) -> None:
        self._settings = settings
        self._allowed_chat_ids = {settings.telegram_chat_id}
        self._handlers = CommandHandlers(settings=settings, **dependencies)

        self._app = (
            Application.builder()
            .token(settings.telegram_bot_token.get_secret_value())
            .build()
        )
        self._register_handlers()

    def _register_handlers(self) -> None:
        app = self._app

        # 인증 미들웨어 (모든 명령에 자동 적용)
        app.add_handler(CommandHandler("start", self._auth_wrap(self._handlers.cmd_start)))
        app.add_handler(CommandHandler("status", self._auth_wrap(self._handlers.cmd_status)))
        app.add_handler(CommandHandler("halt", self._auth_wrap(self._handlers.cmd_halt)))
        app.add_handler(CommandHandler("resume", self._auth_wrap(self._handlers.cmd_resume)))
        app.add_handler(CommandHandler("strategy", self._auth_wrap(self._handlers.cmd_strategy)))
        app.add_handler(CommandHandler("backtest", self._auth_wrap(self._handlers.cmd_backtest)))
        app.add_handler(CommandHandler("logs", self._auth_wrap(self._handlers.cmd_logs)))
        app.add_handler(CommandHandler("panic_sell", self._auth_wrap(self._handlers.cmd_panic_sell)))

        # 인라인 버튼 콜백
        app.add_handler(CallbackQueryHandler(self._auth_wrap(self._handlers.handle_callback)))

        logger.info("telegram_handlers_registered")

    def _auth_wrap(self, handler):
        """채팅 ID 기반 인증 미들웨어"""
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
            chat_id = str(update.effective_chat.id)
            if chat_id not in self._allowed_chat_ids:
                logger.warning(
                    "unauthorized_telegram_access",
                    chat_id=chat_id,
                    user=update.effective_user.username,
                )
                await update.message.reply_text("⛔ 인증되지 않은 접근입니다.")
                return
            return await handler(update, context)
        return wrapper

    async def start(self) -> None:
        """봇 시작 (폴링 모드)"""
        logger.info("telegram_bot_starting")
        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling(
            drop_pending_updates=True,
            allowed_updates=["message", "callback_query"],
        )
        logger.info("telegram_bot_started")

    async def stop(self) -> None:
        """봇 정상 종료"""
        logger.info("telegram_bot_stopping")
        await self._app.updater.stop()
        await self._app.stop()
        await self._app.shutdown()

    async def send_alert(self, message: str) -> None:
        """비동기 알림 발송 (킬스위치 이벤트 등)"""
        try:
            await self._app.bot.send_message(
                chat_id=self._settings.telegram_chat_id,
                text=message,
                parse_mode="HTML",
            )
        except Exception as e:
            logger.error("telegram_send_failed", error=str(e))
