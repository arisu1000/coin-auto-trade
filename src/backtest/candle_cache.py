"""
캔들 데이터 로컬 캐시

Parquet 파일로 저장하여 백테스트 실행 시 API 재호출을 방지한다.

캐시 파일 경로: data/candles/{MARKET}_{UNIT}m.parquet
  예) data/candles/KRW-BTC_1m.parquet

동작 방식:
  - 캐시 없음   → 전체 구간 다운로드 후 저장
  - 캐시 있음   → 캐시의 최신 시각 이후 데이터만 추가 다운로드 후 병합
  - 충분히 최신  → 다운로드 없이 캐시만 반환
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

CACHE_DIR = Path(__file__).parent.parent.parent / "data" / "candles"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# 캐시가 이 시간 이내면 추가 다운로드 없이 바로 반환
_FRESH_THRESHOLD = timedelta(minutes=5)


def _cache_path(market: str, unit: int) -> Path:
    safe_market = market.replace("-", "_")
    return CACHE_DIR / f"{safe_market}_{unit}m.parquet"


def load_cache(market: str, unit: int) -> pd.DataFrame | None:
    """저장된 캐시를 읽어 반환. 없으면 None."""
    path = _cache_path(market, unit)
    if not path.exists():
        return None
    df = pd.read_parquet(path)
    df.index = pd.to_datetime(df.index, utc=True)
    return df.sort_index()


def save_cache(market: str, unit: int, df: pd.DataFrame) -> None:
    """DataFrame을 Parquet 파일로 저장."""
    path = _cache_path(market, unit)
    df.sort_index().to_parquet(path)


def merge_and_save(market: str, unit: int, new_df: pd.DataFrame) -> pd.DataFrame:
    """기존 캐시에 새 데이터를 병합하여 저장하고 반환."""
    cached = load_cache(market, unit)
    if cached is not None:
        merged = pd.concat([cached, new_df])
        merged = merged[~merged.index.duplicated(keep="last")].sort_index()
    else:
        merged = new_df.sort_index()
    save_cache(market, unit, merged)
    return merged


def is_cache_fresh(market: str, unit: int) -> bool:
    """캐시의 마지막 봉이 5분 이내면 True (추가 다운로드 불필요)."""
    cached = load_cache(market, unit)
    if cached is None or cached.empty:
        return False
    latest = cached.index[-1]
    now = datetime.now(tz=timezone.utc)
    return (now - latest) <= _FRESH_THRESHOLD


def get_missing_range(
    market: str,
    unit: int,
    days: int,
) -> tuple[datetime | None, datetime]:
    """
    필요한 구간 중 캐시에 없는 시작 시각을 반환.

    Returns:
        (fetch_from, fetch_until)
        fetch_from=None 이면 전체 구간을 새로 다운로드해야 함.
    """
    now = datetime.now(tz=timezone.utc)
    need_from = now - timedelta(days=days)

    cached = load_cache(market, unit)
    if cached is None or cached.empty:
        return None, now

    earliest = cached.index[0]
    latest = cached.index[-1]

    # 캐시가 요청 구간을 완전히 커버하면 최신 이후만 갱신
    if earliest <= need_from:
        if (now - latest) <= _FRESH_THRESHOLD:
            return latest, latest  # 갱신 불필요 신호 (동일 값)
        return latest, now

    # 캐시가 요청 구간보다 짧으면 전체 재수집
    return None, now


def slice_for_days(market: str, unit: int, days: int) -> pd.DataFrame | None:
    """캐시에서 최근 days일치 데이터만 잘라서 반환."""
    cached = load_cache(market, unit)
    if cached is None or cached.empty:
        return None
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=days)
    sliced = cached[cached.index >= cutoff]
    return sliced if not sliced.empty else None
