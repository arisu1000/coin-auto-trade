# ─────────────────────────────────────────────
# Stage 1: Build TA-Lib C library for ARM64
# ─────────────────────────────────────────────
FROM python:3.11-slim-bookworm AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential wget ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /tmp/ta-lib-build

# TA-Lib 0.4.0 소스 다운로드 및 ARM64 네이티브 컴파일
RUN wget -q https://downloads.sourceforge.net/project/ta-lib/ta-lib/0.4.0/ta-lib-0.4.0-src.tar.gz \
    && tar -xzf ta-lib-0.4.0-src.tar.gz \
    && cd ta-lib \
    && ./configure --prefix=/usr --build=aarch64-unknown-linux-gnu \
    && make -j$(nproc) \
    && make install

# Python 의존성 wheel 빌드 (네이티브 C 라이브러리 사용)
COPY requirements.txt /tmp/requirements.txt
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r /tmp/requirements.txt

# ─────────────────────────────────────────────
# Stage 2: Runtime image (최소 크기)
# ─────────────────────────────────────────────
FROM python:3.11-slim-bookworm AS runtime

# TA-Lib 공유 라이브러리 복사
COPY --from=builder /usr/lib/libta_lib* /usr/lib/
COPY --from=builder /usr/include/ta-lib /usr/include/ta-lib

# 빌드된 Python wheel 설치
COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir --no-index --find-links=/wheels /wheels/*.whl \
    && rm -rf /wheels

# 비루트 사용자 생성 (보안)
RUN adduser --system --no-create-home --group appuser

WORKDIR /app
COPY --chown=appuser:appuser . .

# TA-Lib 링크 경로 설정
ENV TA_LIBRARY_PATH=/usr/lib
ENV TA_INCLUDE_PATH=/usr/include/ta-lib
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONPATH=/app

USER appuser

HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD python -c "import talib; import src" || exit 1

ENTRYPOINT ["/app/docker/scripts/entrypoint.sh"]
CMD ["python", "-m", "src.core.trader"]
