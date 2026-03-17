"""
롱/숏 피라미딩 백테스트 및 롱 전용과 비교

사용법:
    # 롱/숏만 실행
    python scripts/run_backtest_ls.py --data data/candles/KRW_BTC_365d.parquet

    # 파라미터 지정
    python scripts/run_backtest_ls.py --data data/candles/KRW_BTC_365d.parquet \\
        --params entry_pct=5.0 stop_pct=3.0 trail_pct=8.0 add_pct=5.0 unit_amount=200000

    # 롱 전용과 나란히 비교
    python scripts/run_backtest_ls.py --data data/candles/KRW_BTC_365d.parquet --compare \\
        --params entry_pct=5.0 stop_pct=3.0 trail_pct=8.0 add_pct=5.0 unit_amount=200000
"""
import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))


def load_data(path: Path) -> pd.DataFrame:
    if not path.exists():
        print(f"오류: 파일 없음 → {path}")
        print("먼저 데이터를 다운로드하세요:")
        print("  python scripts/download_candles.py --market KRW-BTC --days 365")
        sys.exit(1)
    df = pd.read_parquet(path)
    df.index = pd.to_datetime(df.index, utc=True)
    return df.sort_index()


def parse_params(raw: list[str]) -> dict:
    result = {}
    for item in raw:
        if "=" not in item:
            continue
        key, _, val = item.partition("=")
        key, val = key.strip(), val.strip()
        try:
            result[key] = int(val)
        except ValueError:
            try:
                result[key] = float(val)
            except ValueError:
                result[key] = val
    return result


def run_ls(df, strategy, capital):
    from src.backtest.engine_ls import LSBacktestEngine
    from src.backtest.fees import FeeSchedule
    from src.backtest.slippage import ConservativeSlippage
    from src.config.settings import get_settings
    settings = get_settings()
    engine = LSBacktestEngine(
        strategy=strategy,
        slippage=ConservativeSlippage(),
        fee=FeeSchedule(rate_bps=settings.default_fee_bps),
    )
    return engine.run(df, initial_capital=capital)


def run_long(df, strategy, capital):
    from src.backtest.engine import BacktestEngine
    from src.backtest.fees import FeeSchedule
    from src.backtest.slippage import ConservativeSlippage
    from src.config.settings import get_settings
    settings = get_settings()
    engine = BacktestEngine(
        strategy=strategy,
        slippage=ConservativeSlippage(),
        fee=FeeSchedule(rate_bps=settings.default_fee_bps),
    )
    return engine.run(df, initial_capital=capital)


def print_comparison(result_long, result_ls):
    sl = result_long.report.summary()
    ss = result_ls.report.summary()
    sep = "=" * 54
    print(f"\n{sep}")
    print(f"{'항목':<24} {'롱 전용':>12} {'롱/숏':>12}")
    print("-" * 50)
    rows = [
        ("총 수익률 (%)",   f"{sl['total_return_pct']:+.2f}%",   f"{ss['total_return_pct']:+.2f}%"),
        ("MDD (%)",        f"{sl['max_drawdown_pct']:.2f}%",    f"{ss['max_drawdown_pct']:.2f}%"),
        ("샤프 지수",       f"{sl['sharpe_ratio']:.3f}",         f"{ss['sharpe_ratio']:.3f}"),
        ("수익 팩터",       f"{sl['profit_factor']:.3f}",        f"{ss['profit_factor']:.3f}"),
        ("승률 (%)",       f"{sl['win_rate_pct']:.1f}%",        f"{ss['win_rate_pct']:.1f}%"),
        ("거래 횟수",       f"{sl['total_trades']}",             f"{ss['total_trades']}"),
        ("최종 자산 (원)",  f"{sl['final_capital']:,.0f}",       f"{ss['final_capital']:,.0f}"),
    ]
    for label, v1, v2 in rows:
        print(f"{label:<24} {v1:>12} {v2:>12}")
    print(sep)


def main() -> None:
    parser = argparse.ArgumentParser(description="롱/숏 피라미딩 백테스트")
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--capital", type=float, default=1_000_000)
    parser.add_argument("--compare", action="store_true",
                        help="롱 전용 전략과 나란히 비교")
    parser.add_argument("--params", nargs="*", default=[], metavar="KEY=VALUE")
    args = parser.parse_args()

    df = load_data(args.data)
    params = parse_params(args.params)

    print(f"\n=== 롱/숏 피라미딩 백테스트 ===")
    print(f"데이터: {df.index[0].date()} ~ {df.index[-1].date()} ({len(df):,}개 봉)")
    print(f"자본금: {args.capital:,.0f}원")
    if params:
        print(f"파라미터: {params}")

    from src.strategy.pyramid_breakout_ls import PyramidBreakoutLSStrategy
    ls_strategy = PyramidBreakoutLSStrategy(**params) if params else PyramidBreakoutLSStrategy()

    result_ls = run_ls(df, ls_strategy, args.capital)

    print("\n" + "=" * 48)
    print("[ 롱/숏 (pyramid_breakout_ls) ]")
    print("=" * 48)
    print(result_ls.report.summary_text())

    if args.compare:
        from src.strategy.pyramid_breakout import PyramidBreakoutStrategy
        long_strategy = PyramidBreakoutStrategy(**params) if params else PyramidBreakoutStrategy()
        result_long = run_long(df, long_strategy, args.capital)

        print("\n" + "=" * 48)
        print("[ 롱 전용 (pyramid_breakout) ]")
        print("=" * 48)
        print(result_long.report.summary_text())

        print_comparison(result_long, result_ls)


if __name__ == "__main__":
    main()
