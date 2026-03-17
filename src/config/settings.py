from functools import lru_cache
from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # ─── 업비트 API ───
    upbit_access_key: SecretStr
    upbit_secret_key: SecretStr
    upbit_base_url: str = "https://api.upbit.com/v1"
    upbit_ws_url: str = "wss://api.upbit.com/websocket/v1"

    # ─── API Rate Limit ───
    # 업비트 Exchange API: 초당 8회, 분당 200회
    rate_limit_rps: float = 7.0      # 안전 마진 포함
    rate_limit_burst: int = 10
    backoff_base_seconds: float = 1.0
    backoff_max_seconds: float = 60.0
    backoff_max_retries: int = 5

    # ─── LLM ───
    openai_api_key: SecretStr
    llm_model: str = "gpt-4o-mini"
    llm_temperature: float = 0.1

    # ─── 텔레그램 ───
    telegram_bot_token: SecretStr
    telegram_chat_id: str

    # ─── 매매 설정 ───
    target_markets: str = "KRW-BTC,KRW-ETH,KRW-SOL"
    default_strategy: str = "momentum"
    trade_interval_seconds: int = 60
    trading_mode: str = "paper"   # paper | live

    # ─── 리스크 관리 ───
    macro_max_drawdown_pct: float = 15.0
    micro_stop_loss_pct: float = 3.0
    max_position_pct: float = 30.0

    # ─── 백테스트 ───
    default_fee_bps: int = 5
    default_slippage_bps: int = 3

    # ─── 데이터베이스 ───
    db_path: str = "data/db/trading.db"

    # ─── 로깅 ───
    log_level: str = "INFO"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    @field_validator("trading_mode")
    @classmethod
    def validate_mode(cls, v: str) -> str:
        if v not in ("paper", "live"):
            raise ValueError("trading_mode은 'paper' 또는 'live'여야 합니다")
        return v

    @property
    def markets_list(self) -> list[str]:
        return [m.strip() for m in self.target_markets.split(",")]

    @property
    def is_paper(self) -> bool:
        return self.trading_mode == "paper"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
