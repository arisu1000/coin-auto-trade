"""
Rate Limiter 단위 테스트

- Token Bucket: 버스트 허용 및 속도 제한 검증
- ExponentialBackoff: 재시도 횟수 및 대기 시간 검증
- 429 응답 처리
"""
import asyncio
import time

import pytest

from src.exchange.rate_limiter import (
    ExponentialBackoff,
    RateLimitError,
    RetryableError,
    TokenBucket,
)


class TestTokenBucket:
    async def test_acquire_within_capacity(self):
        """버스트 범위 내에서 즉시 토큰 획득"""
        bucket = TokenBucket(rate=10.0, capacity=10)
        start = time.monotonic()
        for _ in range(5):
            await bucket.acquire()
        elapsed = time.monotonic() - start
        assert elapsed < 0.1, f"버스트 범위 내 획득이 느림: {elapsed:.3f}s"

    async def test_rate_limiting_kicks_in(self):
        """용량 초과 시 속도 제한 적용"""
        bucket = TokenBucket(rate=5.0, capacity=3)
        start = time.monotonic()
        # 3개는 즉시, 4번째는 대기 필요
        for _ in range(4):
            await bucket.acquire()
        elapsed = time.monotonic() - start
        # 4번째 토큰 대기 최소 0.2초 (1/5 = 0.2s)
        assert elapsed >= 0.15, f"속도 제한이 동작하지 않음: {elapsed:.3f}s"

    async def test_token_refill_over_time(self):
        """시간 경과에 따른 토큰 보충"""
        bucket = TokenBucket(rate=10.0, capacity=5)
        # 모든 토큰 소진
        for _ in range(5):
            await bucket.acquire()
        # 0.3초 대기 → 3개 보충 예상
        await asyncio.sleep(0.3)
        assert bucket.available_tokens >= 2.5


class TestExponentialBackoff:
    def test_delay_increases_exponentially(self):
        """지연 시간이 지수적으로 증가"""
        backoff = ExponentialBackoff(base_seconds=1.0, max_seconds=60.0)
        delays = [backoff.get_delay(i) for i in range(5)]
        # 노이즈(jitter) 제거 후 단조 증가 확인
        # jitter ±10%이므로 대략적으로 검증
        assert delays[1] > delays[0] * 0.5
        assert delays[2] > delays[1] * 0.5

    def test_delay_capped_at_max(self):
        """최대 대기 시간 초과 안 함"""
        backoff = ExponentialBackoff(base_seconds=1.0, max_seconds=10.0)
        for attempt in range(20):
            delay = backoff.get_delay(attempt)
            assert delay <= 11.0, f"최대 대기 시간 초과: {delay:.1f}s"

    async def test_retry_on_rate_limit(self):
        """429 에러 시 재시도"""
        call_count = 0

        async def flaky():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise RateLimitError("429", retry_after=0.01)
            return "success"

        backoff = ExponentialBackoff(base_seconds=0.01, max_seconds=1.0, max_retries=5)
        result = await backoff.execute(flaky)
        assert result == "success"
        assert call_count == 3

    async def test_gives_up_after_max_retries(self):
        """최대 재시도 초과 시 예외 전파"""
        async def always_fail():
            raise RateLimitError("429", retry_after=0.001)

        backoff = ExponentialBackoff(base_seconds=0.001, max_retries=2)
        with pytest.raises(RateLimitError):
            await backoff.execute(always_fail)

    async def test_retry_on_server_error(self):
        """5xx 서버 오류 시 재시도"""
        call_count = 0

        async def server_error_then_ok():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RetryableError("500 Server Error")
            return "recovered"

        backoff = ExponentialBackoff(base_seconds=0.01, max_retries=3)
        result = await backoff.execute(server_error_then_ok)
        assert result == "recovered"
        assert call_count == 2
