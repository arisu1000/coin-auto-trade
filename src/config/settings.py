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
    target_markets_top_n: int = 0   # 0이면 TARGET_MARKETS 사용, N이면 24h 거래대금 상위 N개 자동 선택
    default_strategy: str = "momentum"
    trade_interval_seconds: int = 60
    candle_unit_minutes: int = 1         # 진입 탐색용 캔들 단위(분): 0=일봉, 1, 3, 5, 10, 15, 30, 60, 240
    candle_unit_position_minutes: int = 1  # 포지션 보유 중 캔들 단위(분): 0=일봉, 1, 3, 5, 10, 15, 30, 60, 240
    candle_count: int = 100              # 가져올 캔들 수 (최대 200)
    trading_mode: str = "paper"   # paper | live

    # ─── 리스크 관리 ───
    macro_max_drawdown_pct: float = 15.0
    micro_stop_loss_pct: float = 3.0
    max_position_pct: float = 30.0

    # ─── 피라미딩 전략 ───
    pyramid_unit_amount: float = 100_000.0   # 1회 투입 금액(원)
    pyramid_entry_pct: float = 10.0          # 저점 대비 진입 상승률 (%)
    pyramid_add_pct: float = 10.0            # 진입가 대비 추가매수 간격 (%)
    pyramid_stop_pct: float = 10.0           # 진입가 대비 손절 하락률 (%)
    pyramid_trail_pct: float = 10.0          # 최고가 대비 트레일링 스탑 하락률 (%)
    pyramid_sell_cooldown_minutes: int = 1440  # 매도 후 재진입 대기 시간 (분, 기본 24시간)

    # ─── 매수 제외 마켓 ───
    excluded_markets: str = ""               # 매수를 하지 않을 마켓 목록 (쉼표 구분, e.g. KRW-BTC,KRW-ETH)

    # ─── 잔고 경고 ───
    min_krw_alert: float = 10_000.0          # 원화 잔고가 이 금액 이하일 때 텔레그램 경고 발송

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

    _VALID_CANDLE_UNITS = (0, 1, 3, 5, 10, 15, 30, 60, 240)

    @field_validator("candle_unit_minutes", "candle_unit_position_minutes")
    @classmethod
    def validate_candle_unit(cls, v: int) -> int:
        if v not in (0, 1, 3, 5, 10, 15, 30, 60, 240):
            raise ValueError("캔들 단위는 0(일봉), 1, 3, 5, 10, 15, 30, 60, 240 중 하나여야 합니다")
        return v

    @field_validator("candle_count")
    @classmethod
    def validate_candle_count(cls, v: int) -> int:
        if not (1 <= v <= 200):
            raise ValueError("candle_count는 1 이상 200 이하여야 합니다")
        return v

    @property
    def markets_list(self) -> list[str]:
        return [m.strip() for m in self.target_markets.split(",")]

    @property
    def excluded_markets_list(self) -> list[str]:
        return [m.strip().upper() for m in self.excluded_markets.split(",") if m.strip()]

    @property
    def is_paper(self) -> bool:
        return self.trading_mode == "paper"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
