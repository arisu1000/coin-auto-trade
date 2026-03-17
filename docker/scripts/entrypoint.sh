#!/bin/bash
set -e

# SIGTERM/SIGINT를 Python 프로세스로 전달 (Graceful Shutdown)
handle_signal() {
    echo "[entrypoint] 종료 시그널 수신 - 트레이더 정상 종료 중..."
    if [ -n "$CHILD_PID" ]; then
        kill -TERM "$CHILD_PID" 2>/dev/null
        wait "$CHILD_PID"
    fi
    echo "[entrypoint] 정상 종료 완료"
    exit 0
}

trap 'handle_signal' SIGTERM SIGINT

echo "[entrypoint] 코인 자동매매 시스템 시작 (PID: $$)"
echo "[entrypoint] Python: $(python --version)"
echo "[entrypoint] TA-Lib: $(python -c 'import talib; print(talib.__version__)')"

# 사전 환경 체크
python scripts/check_env.py || { echo "[entrypoint] 환경 체크 실패"; exit 1; }

# DB 마이그레이션 실행
python scripts/seed_db.py

# 메인 프로세스 실행 (백그라운드로 실행하여 시그널 처리 가능하게)
"$@" &
CHILD_PID=$!

wait "$CHILD_PID"
EXIT_CODE=$?

echo "[entrypoint] 프로세스 종료 (코드: $EXIT_CODE)"
exit $EXIT_CODE
