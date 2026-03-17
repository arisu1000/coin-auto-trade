"""
Token Bucket + Exponential Backoff Rate Limiter

업비트 API 호출 속도 제한 대응:
- Quotation API: 초당 10회, 분당 600회
- Exchange API: 초당 8회, 분당 200회

설계: Token Bucket이 버스트 허용 후 정상 속도로 복구,
      429/5xx 응답 시 Exponential Backoff로 재시도
"""
import asyncio
import random
import time
from functools import wraps
from typing import Callable, TypeVar

import structlog

logger = structlog.get_logger(__name__)

F = TypeVar("F")


class TokenBucket:
    """
    Token Bucket 알고리즘 기반 비동기 Rate Limiter

    - capacity: 최대 버스트 토큰 수
    - rate: 초당 토큰 보충 속도
    """

    def __init__(self, rate: float, capacity: int) -> None:
        self._rate = rate
        self._capacity = capacity
        self._tokens = float(capacity)
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, tokens: int = 1) -> None:
        """토큰을 획득할 때까지 비동기 대기"""
        while True:
            async with self._lock:
                self._refill()
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return
                # 토큰 부족 시 필요 대기 시간 계산
                wait_time = (tokens - self._tokens) / self._rate

            await asyncio.sleep(wait_time)

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
        self._last_refill = now

    @property
    def available_tokens(self) -> float:
        self._refill()
        return self._tokens


class ExponentialBackoff:
    """
    지수 백오프 재시도 로직

    대기 시간: min(base * 2^attempt + jitter, max_seconds)
    jitter: ±10% 무작위 편차 (Thundering Herd 방지)
    """

    def __init__(
        self,
        base_seconds: float = 1.0,
        max_seconds: float = 60.0,
        max_retries: int = 5,
    ) -> None:
        self._base = base_seconds
        self._max = max_seconds
        self._max_retries = max_retries

    def get_delay(self, attempt: int) -> float:
        delay = min(self._base * (2 ** attempt), self._max)
        jitter = random.uniform(-0.1 * delay, 0.1 * delay)
        return max(0.0, delay + jitter)

    async def execute(self, func: Callable, *args, **kwargs):
        """재시도 로직 포함 함수 실행"""
        last_exc = None
        for attempt in range(self._max_retries + 1):
            try:
                return await func(*args, **kwargs)
            except RateLimitError as e:
                last_exc = e
                if attempt >= self._max_retries:
                    break
                delay = e.retry_after or self.get_delay(attempt)
                logger.warning(
                    "rate_limit_hit",
                    attempt=attempt,
                    retry_after=delay,
                    url=str(getattr(e, "url", "")),
                )
                await asyncio.sleep(delay)
            except RetryableError as e:
                last_exc = e
                if attempt >= self._max_retries:
                    break
                delay = self.get_delay(attempt)
                logger.warning(
                    "retryable_error",
                    attempt=attempt,
                    delay=delay,
                    error=str(e),
                )
                await asyncio.sleep(delay)

        raise last_exc or RuntimeError("Max retries exceeded")


class RateLimitError(Exception):
    """HTTP 429 Too Many Requests"""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class RetryableError(Exception):
    """재시도 가능한 서버 오류 (5xx)"""
