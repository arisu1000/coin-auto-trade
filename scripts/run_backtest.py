"""
백테스트 CLI

사용법:
    python scripts/run_backtest.py --strategy momentum --market KRW-BTC --days 30
    python scripts/run_backtest.py --strategy mean_reversion --market KRW-ETH --days 90 --capital 5000000
"""
import argparse
import asyncio
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))


async def fetch_candles(market: str, days: int) -> pd.DataFrame:
    """업비트에서 과거 분봉 데이터 수집 (Rate Limit 준수)"""
    from src.config.settings import get_settings
    from src.exchange.upbit_client import UpbitClient

    settings = get_settings()
    all_candles = []

    async with UpbitClient(settings) as client:
        # 200개씩 페이지네이션 (1분봉 기준: 200분 = 3.3시간)
        target_count = days * 24 * 60
        to_param = None

        print(f"  {market} {days}일치 데이터 수집 중 (목표: {target_count:,}개 캔들)...")
        while len(all_candles) < target_count:
            candles = await client.get_candles_minutes(
                market, unit=1, count=200, to=to_param
            )
            if not candles:
                break
            all_candles.extend(candles)
            to_param = candles[-1].timestamp.strftime("%Y-%m-%dT%H:%M:%S")
            print(f"  수집 완료: {len(all_candles):,}/{target_count:,}")
            await asyncio.sleep(0.2)  # Rate Limit 준수

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

    # 중복 타임스탬프 제거 (REST + WebSocket 이중 계상 방어)
    df = df[~df.index.duplicated(keep="first")]
    return df


async def main() -> None:
    parser = argparse.ArgumentParser(description="코인 자동매매 백테스트")
    parser.add_argument("--strategy", default="momentum", help="전략 이름")
    parser.add_argument("--market", default="KRW-BTC", help="마켓 코드")
    parser.add_argument("--days", type=int, default=30, help="백테스트 기간 (일)")
    parser.add_argument("--capital", type=float, default=1_000_000, help="초기 자본금 (원)")
    parser.add_argument("--slippage", choices=["fixed", "conservative"], default="conservative")
    args = parser.parse_args()

    print(f"\n=== 백테스트 시작 ===")
    print(f"전략: {args.strategy} | 마켓: {args.market} | 기간: {args.days}일 | 자본: {args.capital:,.0f}원")
    print()

    # 데이터 수집
    df = await fetch_candles(args.market, args.days)
    print(f"\n데이터 수집 완료: {len(df):,}개 봉 ({df.index[0]} ~ {df.index[-1]})")

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

    print("\n" + "=" * 50)
    print(result.report.summary_text())
    print("=" * 50)


if __name__ == "__main__":
    asyncio.run(main())
