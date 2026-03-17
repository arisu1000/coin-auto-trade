"""
백테스트 CLI

사용법:
    # 저장된 데이터 파일로 백테스트 (권장)
    python scripts/run_backtest.py --data data/candles/KRW_BTC_90d.parquet --strategy momentum
    python scripts/run_backtest.py --data data/candles/KRW_BTC_90d.parquet --strategy turtle

    # 데이터를 직접 다운로드하며 백테스트 (느림)
    python scripts/run_backtest.py --market KRW-BTC --days 30 --strategy momentum
"""
import argparse
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))


def load_data(data_path: Path) -> pd.DataFrame:
    """Parquet 파일에서 캔들 데이터를 로드한다."""
    if not data_path.exists():
        print(f"오류: 파일을 찾을 수 없습니다 → {data_path}")
        print("먼저 데이터를 다운로드하세요:")
        print(f"  python scripts/download_candles.py --market KRW-BTC --days 90")
        sys.exit(1)

    df = pd.read_parquet(data_path)
    df.index = pd.to_datetime(df.index, utc=True)
    return df.sort_index()


async def download_data(market: str, days: int) -> pd.DataFrame:
    """업비트에서 캔들 데이터를 직접 다운로드한다."""
    from src.config.settings import get_settings
    from src.exchange.upbit_client import UpbitClient

    settings = get_settings()
    all_candles = []
    target_count = days * 24 * 60

    print(f"  {market} {days}일치 데이터 다운로드 중 (목표: {target_count:,}개 캔들)...")

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

    print()

    if not all_candles:
        raise RuntimeError("캔들 데이터를 가져오지 못했습니다")

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


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="코인 자동매매 백테스트",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
예시:
  # 저장된 데이터 파일로 (권장)
  python scripts/run_backtest.py --data data/candles/KRW_BTC_90d.parquet --strategy momentum
  python scripts/run_backtest.py --data data/candles/KRW_BTC_90d.parquet --strategy turtle --capital 5000000

  # 데이터를 직접 다운로드
  python scripts/run_backtest.py --market KRW-BTC --days 30 --strategy momentum
        """,
    )
    parser.add_argument("--strategy", default="momentum", help="전략 이름")
    parser.add_argument("--capital", type=float, default=1_000_000, help="초기 자본금 원 (기본: 1,000,000)")
    parser.add_argument("--slippage", choices=["fixed", "conservative"], default="conservative")

    data_group = parser.add_mutually_exclusive_group()
    data_group.add_argument("--data", type=Path, help="사용할 Parquet 데이터 파일 경로")
    data_group.add_argument("--market", default=None, help="직접 다운로드할 마켓 코드")

    parser.add_argument("--days", type=int, default=30, help="--market 사용 시 다운로드 기간 (일)")
    args = parser.parse_args()

    # 데이터 소스가 없으면 기본값 안내
    if args.data is None and args.market is None:
        parser.print_help()
        print("\n오류: --data 또는 --market 중 하나를 지정하세요.")
        sys.exit(1)

    print(f"\n=== 백테스트 시작 ===")
    print(f"전략: {args.strategy} | 자본: {args.capital:,.0f}원")

    # 데이터 준비
    if args.data:
        print(f"데이터: {args.data}")
        df = load_data(args.data)
    else:
        print(f"데이터: {args.market} {args.days}일치 (다운로드)")
        df = await download_data(args.market, args.days)

    print(f"기간: {df.index[0].date()} ~ {df.index[-1].date()} ({len(df):,}개 봉)\n")

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
        else FixedBpsSlippage(bps=settings.default_slippage_bps)
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

    print("=" * 50)
    print(result.report.summary_text())
    print("=" * 50)


if __name__ == "__main__":
    asyncio.run(main())
