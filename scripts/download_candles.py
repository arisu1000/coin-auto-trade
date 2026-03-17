"""
캔들 데이터 다운로드 스크립트

업비트에서 과거 캔들 데이터를 받아 Parquet 파일로 저장한다.
기존 파일이 있으면 덮어쓰지 않고 누락된 구간만 추가로 받아 병합한다.

  첫 실행  → 전체 다운로드 후 저장
  재실행   → 마지막 봉 이후 최신 데이터만 추가
  --days 늘림 → 기존 첫 봉 이전 과거 데이터도 추가

사용법:
    python scripts/download_candles.py --market KRW-BTC --days 90
    python scripts/download_candles.py --market KRW-ETH --days 30 --out data/candles/eth_30d.parquet
"""
import argparse
import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

DEFAULT_OUT_DIR = Path(__file__).parent.parent / "data" / "candles"
_FRESH_MINUTES = 5  # 마지막 봉이 이 시간 이내면 최신으로 간주


def _load_existing(path: Path) -> pd.DataFrame | None:
    if not path.exists():
        return None
    df = pd.read_parquet(path)
    df.index = pd.to_datetime(df.index, utc=True)
    return df.sort_index()


def _to_df(candles: list) -> pd.DataFrame:
    data = [
        {
            "timestamp": c.timestamp,
            "open": c.open, "high": c.high, "low": c.low,
            "close": c.close, "volume": c.volume,
        }
        for c in candles
    ]
    df = pd.DataFrame(data).set_index("timestamp")
    df.index = pd.to_datetime(df.index, utc=True)
    return df.sort_index()


async def _fetch_backward(
    client,
    market: str,
    stop_at: datetime,
    start_from: datetime | None = None,
    label: str = "",
) -> list:
    """
    start_from 시각부터 역방향으로 캔들을 수집하여 stop_at 직전까지 반환한다.

    Args:
        start_from : 이 시각 직전부터 다운로드 (None이면 현재부터)
        stop_at    : 이 시각 이하의 봉은 버리고 중단
    """
    collected = []
    to_param = start_from.strftime("%Y-%m-%dT%H:%M:%S") if start_from else None

    while True:
        candles = await client.get_candles_minutes(market, unit=1, count=200, to=to_param)
        if not candles:
            break

        keep = []
        done = False
        for c in candles:
            c_ts = pd.Timestamp(c.timestamp).tz_localize("UTC") if c.timestamp.tzinfo is None \
                   else pd.Timestamp(c.timestamp).tz_convert("UTC")
            if c_ts <= stop_at:
                done = True
                break
            keep.append(c)

        collected.extend(keep)
        print(f"  {label}수집: {len(collected):,}개", end="\r")
        await asyncio.sleep(0.2)

        if done or not keep:
            break

        to_param = candles[-1].timestamp.strftime("%Y-%m-%dT%H:%M:%S")

    return collected


async def download(market: str, days: int, out_path: Path) -> None:
    from src.config.settings import get_settings
    from src.exchange.upbit_client import UpbitClient

    now = datetime.now(tz=timezone.utc)
    target_start = now - timedelta(days=days)

    existing = _load_existing(out_path)

    if existing is not None:
        oldest = existing.index[0]
        latest = existing.index[-1]
        print(f"기존 파일: {len(existing):,}개 봉  ({oldest.date()} ~ {latest.date()})")

        need_newer = (now - latest) > timedelta(minutes=_FRESH_MINUTES)
        need_older = oldest > target_start

        if not need_newer and not need_older:
            print("이미 최신 데이터입니다. 다운로드를 건너뜁니다.")
            return
    else:
        need_newer = False   # 파일 자체가 없으므로 전체 다운로드
        need_older = True
        oldest = now
        latest = None
        print(f"파일 없음. 전체 다운로드 시작: {market} {days}일치")

    settings = get_settings()
    all_new: list = []

    async with UpbitClient(settings) as client:
        # ── 1) 최신 업데이트: latest 이후 ~ 현재 ──────────────────────
        if need_newer and latest is not None:
            print(f"\n[최신 업데이트] {latest.strftime('%Y-%m-%d %H:%M')} 이후 ~")
            recent = await _fetch_backward(client, market, stop_at=latest, label="최신 ")
            if recent:
                all_new.extend(recent)
                print(f"\n  → {len(recent):,}개 추가")
            else:
                print("\n  → 새 데이터 없음")

        # ── 2) 과거 확장: target_start ~ oldest 이전 ──────────────────
        if need_older:
            print(f"\n[과거 확장] ~ {target_start.strftime('%Y-%m-%d')} 까지")
            older = await _fetch_backward(
                client, market,
                stop_at=target_start,
                start_from=oldest if existing is not None else None,
                label="과거 ",
            )
            if older:
                all_new.extend(older)
                print(f"\n  → {len(older):,}개 추가")
            else:
                print("\n  → 추가 데이터 없음")

    if not all_new:
        print("\n새로운 데이터가 없습니다. 기존 파일을 유지합니다.")
        return

    new_df = _to_df(all_new)

    # 기존 데이터와 병합
    if existing is not None:
        merged = pd.concat([existing, new_df])
    else:
        merged = new_df

    merged = merged[~merged.index.duplicated(keep="last")].sort_index()

    out_path.parent.mkdir(parents=True, exist_ok=True)
    merged.to_parquet(out_path)

    added = len(merged) - (len(existing) if existing is not None else 0)
    print(f"\n저장 완료: {len(merged):,}개 봉 ({merged.index[0].date()} ~ {merged.index[-1].date()})")
    print(f"  추가된 봉: {added:+,}개  →  {out_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="업비트 캔들 데이터 다운로드 (누적)")
    parser.add_argument("--market", default="KRW-BTC", help="마켓 코드 (기본: KRW-BTC)")
    parser.add_argument("--days", type=int, default=90, help="목표 수집 기간 (일, 기본: 90)")
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="저장 경로 (기본: data/candles/{MARKET}_{DAYS}d.parquet)",
    )
    args = parser.parse_args()

    if args.out is None:
        safe_market = args.market.replace("-", "_")
        args.out = DEFAULT_OUT_DIR / f"{safe_market}_{args.days}d.parquet"

    asyncio.run(download(args.market, args.days, args.out))


if __name__ == "__main__":
    main()
