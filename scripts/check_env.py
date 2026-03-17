"""
사전 환경 체크 스크립트

컨테이너 시작 전 필수 의존성 및 환경 변수 검증.
실패 시 exit(1)으로 즉시 종료하여 잘못된 설정으로의 시작을 방지.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def check_talib():
    try:
        import talib
        print(f"[✓] TA-Lib {talib.__version__}")
    except ImportError as e:
        print(f"[✗] TA-Lib 로드 실패: {e}")
        return False
    return True


def check_env_vars():
    required = ["UPBIT_ACCESS_KEY", "UPBIT_SECRET_KEY", "OPENAI_API_KEY",
                "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"]
    import os
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        print(f"[✗] 필수 환경 변수 누락: {', '.join(missing)}")
        return False
    print(f"[✓] 환경 변수 ({len(required)}개 확인)")
    return True


def check_settings():
    try:
        from src.config.settings import get_settings
        settings = get_settings()
        print(f"[✓] 설정 로드 완료 (모드: {settings.trading_mode})")
        return True
    except Exception as e:
        print(f"[✗] 설정 로드 실패: {e}")
        return False


def check_db_path():
    try:
        from src.config.settings import get_settings
        settings = get_settings()
        db_dir = Path(settings.db_path).parent
        db_dir.mkdir(parents=True, exist_ok=True)
        print(f"[✓] DB 디렉토리: {db_dir}")
        return True
    except Exception as e:
        print(f"[✗] DB 경로 확인 실패: {e}")
        return False


if __name__ == "__main__":
    checks = [check_talib, check_env_vars, check_settings, check_db_path]
    results = [fn() for fn in checks]

    if all(results):
        print("\n[✓] 모든 사전 체크 통과")
        sys.exit(0)
    else:
        failed = sum(1 for r in results if not r)
        print(f"\n[✗] {failed}개 체크 실패 - 시작 중단")
        sys.exit(1)
