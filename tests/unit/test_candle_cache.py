"""
캔들 캐시 단위 테스트
"""
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from src.backtest.candle_cache import (
    _FRESH_THRESHOLD,
    _cache_path,
    get_missing_range,
    is_cache_fresh,
    load_cache,
    merge_and_save,
    save_cache,
    slice_for_days,
)


def _make_df(start: datetime, minutes: int) -> pd.DataFrame:
    idx = pd.date_range(start, periods=minutes, freq="1min", tz="UTC")
    return pd.DataFrame(
        {"open": 1.0, "high": 1.1, "low": 0.9, "close": 1.0, "volume": 10.0},
        index=idx,
    )


@pytest.fixture()
def tmp_cache_dir(tmp_path, monkeypatch):
    """캐시 디렉터리를 임시 경로로 교체"""
    monkeypatch.setattr("src.backtest.candle_cache.CACHE_DIR", tmp_path)
    return tmp_path


class TestSaveLoad:
    def test_save_and_load_roundtrip(self, tmp_cache_dir):
        now = datetime(2024, 1, 10, tzinfo=timezone.utc)
        df = _make_df(now - timedelta(minutes=10), minutes=10)
        save_cache("KRW-BTC", 1, df)

        loaded = load_cache("KRW-BTC", 1)
        assert loaded is not None
        assert len(loaded) == len(df)
        assert list(loaded.columns) == list(df.columns)

    def test_load_returns_none_when_no_file(self, tmp_cache_dir):
        assert load_cache("KRW-ETH", 1) is None

    def test_cache_path_format(self, tmp_cache_dir):
        path = _cache_path("KRW-BTC", 1)
        assert path.name == "KRW_BTC_1m.parquet"


class TestMergeAndSave:
    def test_merge_appends_new_rows(self, tmp_cache_dir):
        t0 = datetime(2024, 1, 1, tzinfo=timezone.utc)
        old_df = _make_df(t0, minutes=60)
        save_cache("KRW-BTC", 1, old_df)

        new_start = t0 + timedelta(minutes=60)
        new_df = _make_df(new_start, minutes=30)
        merged = merge_and_save("KRW-BTC", 1, new_df)

        assert len(merged) == 90

    def test_merge_deduplicates_overlap(self, tmp_cache_dir):
        t0 = datetime(2024, 1, 1, tzinfo=timezone.utc)
        old_df = _make_df(t0, minutes=60)
        save_cache("KRW-BTC", 1, old_df)

        # 마지막 10개 봉이 겹치는 새 데이터
        overlap_start = t0 + timedelta(minutes=50)
        new_df = _make_df(overlap_start, minutes=30)
        merged = merge_and_save("KRW-BTC", 1, new_df)

        assert len(merged) == 80  # 60 + 30 - 10 중복 = 80

    def test_merge_without_existing_cache(self, tmp_cache_dir):
        t0 = datetime(2024, 1, 1, tzinfo=timezone.utc)
        df = _make_df(t0, minutes=20)
        merged = merge_and_save("KRW-BTC", 1, df)
        assert len(merged) == 20


class TestIsCacheFresh:
    def test_fresh_cache(self, tmp_cache_dir):
        now = datetime.now(tz=timezone.utc)
        df = _make_df(now - timedelta(minutes=2), minutes=2)
        save_cache("KRW-BTC", 1, df)
        assert is_cache_fresh("KRW-BTC", 1) is True

    def test_stale_cache(self, tmp_cache_dir):
        old = datetime.now(tz=timezone.utc) - timedelta(hours=2)
        df = _make_df(old, minutes=10)
        save_cache("KRW-BTC", 1, df)
        assert is_cache_fresh("KRW-BTC", 1) is False

    def test_no_cache_is_not_fresh(self, tmp_cache_dir):
        assert is_cache_fresh("KRW-BTC", 1) is False


class TestGetMissingRange:
    def test_no_cache_returns_none_from(self, tmp_cache_dir):
        fetch_from, _ = get_missing_range("KRW-BTC", 1, days=7)
        assert fetch_from is None

    def test_cache_covers_range_returns_same_when_fresh(self, tmp_cache_dir):
        now = datetime.now(tz=timezone.utc)
        # 10일치 캐시, 최신 봉이 1분 전
        df = _make_df(now - timedelta(days=10), minutes=10 * 24 * 60 - 1)
        save_cache("KRW-BTC", 1, df)

        fetch_from, fetch_until = get_missing_range("KRW-BTC", 1, days=7)
        # 캐시가 최신이면 fetch_from == fetch_until
        assert fetch_from == fetch_until

    def test_cache_earliest_slightly_after_need_from_is_not_redownloaded(self, tmp_cache_dir):
        """API 1분봉 경계 오차로 earliest가 need_from보다 수 분 늦어도 재다운로드 안 함"""
        now = datetime.now(tz=timezone.utc)
        # need_from = now - 7days. 캐시 시작을 need_from보다 30분 늦게 설정 (API 오차 시뮬레이션)
        cache_start = now - timedelta(days=7) + timedelta(minutes=30)
        stale_end = now - timedelta(hours=2)
        minutes = int((stale_end - cache_start).total_seconds() / 60)
        df = _make_df(cache_start, minutes=minutes)
        save_cache("KRW-BTC", 1, df)

        fetch_from, fetch_until = get_missing_range("KRW-BTC", 1, days=7)
        # tolerance(1h) 이내 오차이므로 재다운로드 없이 증분 갱신으로 처리돼야 함
        assert fetch_from is not None  # None이면 전체 재다운로드 → 버그
        assert fetch_from < fetch_until

    def test_stale_cache_returns_latest_as_from(self, tmp_cache_dir):
        now = datetime.now(tz=timezone.utc)
        stale_end = now - timedelta(hours=3)
        df = _make_df(stale_end - timedelta(days=7), minutes=7 * 24 * 60)
        save_cache("KRW-BTC", 1, df)

        fetch_from, fetch_until = get_missing_range("KRW-BTC", 1, days=7)
        assert fetch_from is not None
        assert fetch_from < fetch_until

    def test_short_cache_triggers_full_download(self, tmp_cache_dir):
        now = datetime.now(tz=timezone.utc)
        # 1일치 캐시만 있는데 7일치를 요청 (tolerance 1h보다 훨씬 짧음)
        df = _make_df(now - timedelta(days=1), minutes=24 * 60)
        save_cache("KRW-BTC", 1, df)

        fetch_from, _ = get_missing_range("KRW-BTC", 1, days=7)
        assert fetch_from is None  # 전체 재수집 필요


class TestSliceForDays:
    def test_returns_only_recent_days(self, tmp_cache_dir):
        now = datetime.now(tz=timezone.utc)
        df = _make_df(now - timedelta(days=30), minutes=30 * 24 * 60)
        save_cache("KRW-BTC", 1, df)

        sliced = slice_for_days("KRW-BTC", 1, days=7)
        assert sliced is not None
        cutoff = now - timedelta(days=7)
        assert sliced.index[0] >= cutoff

    def test_returns_none_when_no_cache(self, tmp_cache_dir):
        assert slice_for_days("KRW-BTC", 1, days=7) is None
