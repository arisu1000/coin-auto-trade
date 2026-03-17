"""
백테스트 CLI

사용법:
    python scripts/run_backtest.py --strategy momentum --market KRW-BTC --days 30
    python scripts/run_backtest.py --strategy mean_reversion --market KRW-ETH --days 90 --capital 5000000
    python scripts/run_backtest.py --strategy turtle --market KRW-BTC --days 90 --refresh
"""
import argparse
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))


async def _download_candles(
    market: str,
    days: int,
    *,
    from_dt: datetime | None = None,
) -> pd.DataFrame:
    """
    업비트에서 캔들 데이터를 다운로드한다.

    Args:
        market  : 마켓 코드 (예: KRW-BTC)
        days    : 수집 기간 (일). from_dt가 주어지면 그 이후만 수집.
        from_dt : 이 시각 이후 데이터만 수집 (증분 갱신용)
    """
    from src.config.settings import get_settings
    from src.exchange.upbit_client import UpbitClient

    settings = get_settings()
    all_candles = []

    if from_dt is not None:
        target_count = int((datetime.now(tz=timezone.utc) - from_dt).total_seconds() / 60)
        label = f"{from_dt.strftime('%Y-%m-%d %H:%M')} 이후"
    else:
        target_count = days * 24 * 60
        label = f"{days}일치 전체"

    if target_count <= 0:
        return pd.DataFrame()

    print(f"  {market} {label} 데이터 다운로드 중 (목표: {target_count:,}개 캔들)...")

    async with UpbitClient(settings) as client:
        to_param = None
        stop_dt = from_dt

        while len(all_candles) < target_count:
            candles = await client.get_candles_minutes(
                market, unit=1, count=200, to=to_param
            )
            if not candles:
                break

            # 증분 갱신 시 from_dt 이전 봉은 버림
            if stop_dt is not None:
                candles = [c for c in candles if c.timestamp > stop_dt]
                if not candles:
                    break

            all_candles.extend(candles)
            to_param = candles[-1].timestamp.strftime("%Y-%m-%dT%H:%M:%S")
            print(f"  수집 완료: {len(all_candles):,}/{target_count:,}")
            await asyncio.sleep(0.2)  # Rate Limit 준수

    if not all_candles:
        return pd.DataFrame()

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
    return df[~df.index.duplicated(keep="first")]


async def fetch_candles(market: str, days: int, *, refresh: bool = False) -> pd.DataFrame:
    """
    캐시를 활용하여 캔들 데이터를 반환한다.

    동작 흐름:
      1. --refresh 플래그 → 캐시 무시하고 전체 재다운로드
      2. 캐시 없음         → 전체 다운로드 후 저장
      3. 캐시가 요청 구간보다 짧음 → 전체 재다운로드
      4. 캐시가 오래됨     → 마지막 봉 이후만 증분 다운로드 후 병합
      5. 캐시가 최신       → 다운로드 없이 캐시 반환
    """
    from src.backtest.candle_cache import (
        get_missing_range,
        merge_and_save,
        save_cache,
        slice_for_days,
    )

    cache_path_msg = f"data/candles/{market.replace('-', '_')}_1m.parquet"

    if not refresh:
        fetch_from, fetch_until = get_missing_range(market, unit=1, days=days)

        # 캐시가 충분히 최신 (5분 이내) → 바로 반환
        if fetch_from == fetch_until:
            cached = slice_for_days(market, unit=1, days=days)
            print(f"  캐시 사용 ({cache_path_msg}): {len(cached):,}개 봉")
            return cached

        if fetch_from is not None:
            # 증분 갱신: 마지막 캔들 이후만 다운로드
            print(f"  증분 갱신: {fetch_from.strftime('%Y-%m-%d %H:%M')} 이후 데이터 추가")
            new_df = await _download_candles(market, days, from_dt=fetch_from)
            if not new_df.empty:
                merge_and_save(market, unit=1, new_df=new_df)
                print(f"  캐시 업데이트 완료 ({cache_path_msg})")
            else:
                print("  추가 데이터 없음, 기존 캐시 사용")
            return slice_for_days(market, unit=1, days=days)

    # 전체 다운로드 (캐시 없음 또는 --refresh)
    if refresh:
        print("  --refresh: 기존 캐시 무시하고 전체 재다운로드")
    df = await _download_candles(market, days)
    if df.empty:
        raise RuntimeError("캔들 데이터를 가져오지 못했습니다")
    save_cache(market, unit=1, df=df)
    print(f"  캐시 저장 완료 ({cache_path_msg})")
    return df


async def main() -> None:
    parser = argparse.ArgumentParser(description="코인 자동매매 백테스트")
    parser.add_argument("--strategy", default="momentum", help="전략 이름")
    parser.add_argument("--market", default="KRW-BTC", help="마켓 코드")
    parser.add_argument("--days", type=int, default=30, help="백테스트 기간 (일)")
    parser.add_argument("--capital", type=float, default=1_000_000, help="초기 자본금 (원)")
    parser.add_argument("--slippage", choices=["fixed", "conservative"], default="conservative")
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="캐시를 무시하고 데이터를 새로 다운로드",
    )
    args = parser.parse_args()

    print(f"\n=== 백테스트 시작 ===")
    print(f"전략: {args.strategy} | 마켓: {args.market} | 기간: {args.days}일 | 자본: {args.capital:,.0f}원")
    print()

    # 데이터 수집 (캐시 우선)
    df = await fetch_candles(args.market, args.days, refresh=args.refresh)
    print(f"\n데이터 준비 완료: {len(df):,}개 봉 ({df.index[0]} ~ {df.index[-1]})")

    # 전략 로드
    from src.strategy.manager import StrategyManager
    manager = StrategyManager(Path("src/strategy"))
    strategy = manager.load(args.strategy)

    # 슬리피지 모델 선택
    from src.backtest.slippage import ConservativeSlippage, FixedBpsSlippage
    from src.config.settings import get_settings
    settings = get_settings()
    slippage = (
        ConservativeSlippage() if args.slippage == "conservative"
        else FixedBpsSlippage(bps=settings.default_fee_bps)
    )

    # 백테스트 실행
    from src.backtest.engine import BacktestEngine
    from src.backtest.fees import FeeSchedule
    engine = BacktestEngine(
        strategy=strategy,
        slippage=slippage,
        fee=FeeSchedule(rate_bps=settings.default_fee_bps),
    )
    result = engine.run(df, initial_capital=args.capital)

    print("\n" + "=" * 50)
    print(result.report.summary_text())
    print("=" * 50)


if __name__ == "__main__":
    asyncio.run(main())
