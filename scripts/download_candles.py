"""
캔들 데이터 다운로드 스크립트

업비트에서 과거 캔들 데이터를 받아 Parquet 파일로 저장한다.
저장된 파일은 run_backtest.py의 --data 옵션으로 재사용한다.

사용법:
    python scripts/download_candles.py --market KRW-BTC --days 90
    python scripts/download_candles.py --market KRW-ETH --days 30 --out data/candles/eth_30d.parquet
"""
import argparse
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

DEFAULT_OUT_DIR = Path(__file__).parent.parent / "data" / "candles"


async def download(market: str, days: int, out_path: Path) -> None:
    from src.config.settings import get_settings
    from src.exchange.upbit_client import UpbitClient

    import pandas as pd

    settings = get_settings()
    all_candles = []
    target_count = days * 24 * 60

    print(f"다운로드 시작: {market} {days}일치 ({target_count:,}개 캔들 목표)")
    print(f"저장 경로: {out_path}")

    async with UpbitClient(settings) as client:
        to_param = None
        while len(all_candles) < target_count:
            candles = await client.get_candles_minutes(
                market, unit=1, count=200, to=to_param
            )
            if not candles:
                break
            all_candles.extend(candles)
            to_param = candles[-1].timestamp.strftime("%Y-%m-%dT%H:%M:%S")
            print(f"  수집: {len(all_candles):,}/{target_count:,}", end="\r")
            await asyncio.sleep(0.2)

    print()  # \r 정리

    if not all_candles:
        print("오류: 데이터를 가져오지 못했습니다.")
        sys.exit(1)

    data = [
        {
            "timestamp": c.timestamp,
            "open": c.open, "high": c.high, "low": c.low,
            "close": c.close, "volume": c.volume,
        }
        for c in all_candles
    ]
    df = pd.DataFrame(data).set_index("timestamp").sort_index()
    df.index = pd.to_datetime(df.index, utc=True)
    df = df[~df.index.duplicated(keep="first")]

    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_path)

    print(f"저장 완료: {len(df):,}개 봉 ({df.index[0].date()} ~ {df.index[-1].date()})")
    print(f"  → {out_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="업비트 캔들 데이터 다운로드")
    parser.add_argument("--market", default="KRW-BTC", help="마켓 코드 (기본: KRW-BTC)")
    parser.add_argument("--days", type=int, default=90, help="수집 기간 (일, 기본: 90)")
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
