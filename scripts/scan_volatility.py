"""
변동성 코인 스캐너

업비트 KRW 전체 마켓을 스캔하여 유동성 높고 변동성 큰 코인을 추천한다.

필터 조건:
  - 24시간 거래대금 ≥ MIN_VOLUME (기본 10억 KRW)
  - 24시간 고가-저가 범위 ≥ MIN_RANGE_PCT (기본 15%)

사용법:
    python scripts/scan_volatility.py
    python scripts/scan_volatility.py --min-range 10 --min-volume 5
    python scripts/scan_volatility.py --top 30 --no-select
"""
import argparse
import asyncio
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

ENV_PATH = Path(__file__).parent.parent / ".env"


# ── .env 업데이트 헬퍼 ──────────────────────────────────────────────────────

def _update_env_markets(markets: list[str]) -> None:
    """
    .env 파일의 TARGET_MARKETS 값을 교체한다.
    해당 키가 없으면 파일 끝에 추가한다.
    """
    new_value = ",".join(markets)
    new_line = f"TARGET_MARKETS={new_value}\n"

    if ENV_PATH.exists():
        text = ENV_PATH.read_text(encoding="utf-8")
        if re.search(r"^TARGET_MARKETS\s*=", text, re.MULTILINE):
            text = re.sub(
                r"^TARGET_MARKETS\s*=.*$",
                f"TARGET_MARKETS={new_value}",
                text,
                flags=re.MULTILINE,
            )
        else:
            text = text.rstrip("\n") + "\n" + new_line
    else:
        text = new_line

    ENV_PATH.write_text(text, encoding="utf-8")


# ── 스캔 로직 ───────────────────────────────────────────────────────────────

async def scan(
    min_range_pct: float,
    min_volume_billion: float,
    top: int,
) -> list[dict]:
    """
    KRW 마켓 전체를 스캔하여 조건에 맞는 코인 목록을 반환한다.

    Returns:
        list of dict with keys:
            market, korean_name, price, range_pct, change_pct, volume_billion
        정렬: range_pct 내림차순
    """
    from src.config.settings import get_settings
    from src.exchange.upbit_client import UpbitClient

    settings = get_settings()

    async with UpbitClient(settings) as client:
        # 1) 전체 KRW 마켓 목록
        markets_info = await client.get_markets(krw_only=True)
        market_name = {m["market"]: m["korean_name"] for m in markets_info}
        all_codes = list(market_name.keys())

        # 2) 티커 (한 번에 최대 100개씩 나눠서 조회)
        tickers: list[dict] = []
        chunk_size = 100
        for i in range(0, len(all_codes), chunk_size):
            chunk = all_codes[i : i + chunk_size]
            result = await client.get_ticker(chunk)
            tickers.extend(result)

    # 3) 필터 + 정렬
    results = []
    min_volume_krw = min_volume_billion * 1_000_000_000

    for t in tickers:
        high = float(t.get("high_price", 0) or 0)
        low = float(t.get("low_price", 0) or 0)
        volume = float(t.get("acc_trade_price_24h", 0) or 0)
        price = float(t.get("trade_price", 0) or 0)
        change = float(t.get("signed_change_rate", 0) or 0) * 100

        if low <= 0:
            continue
        range_pct = (high - low) / low * 100

        if range_pct < min_range_pct:
            continue
        if volume < min_volume_krw:
            continue

        results.append({
            "market": t["market"],
            "korean_name": market_name.get(t["market"], ""),
            "price": price,
            "range_pct": range_pct,
            "change_pct": change,
            "volume_billion": volume / 1_000_000_000,
        })

    results.sort(key=lambda x: x["range_pct"], reverse=True)
    return results[:top]


# ── 출력 ────────────────────────────────────────────────────────────────────

def _print_table(results: list[dict], min_range_pct: float, min_volume: float) -> None:
    print(f"\n{'='*72}")
    print(f" 변동성 스캔 결과   "
          f"(24h 범위 ≥ {min_range_pct}%,  거래대금 ≥ {min_volume}B KRW)")
    print(f"{'='*72}")
    header = f"{'#':>3}  {'마켓':<12} {'코인명':<12} {'현재가':>14} "
    header += f"{'24h범위':>8} {'24h등락':>8} {'거래대금(B)':>11}"
    print(header)
    print("-" * 72)

    for i, r in enumerate(results, 1):
        change_sign = "+" if r["change_pct"] >= 0 else ""
        print(
            f"{i:>3}  {r['market']:<12} {r['korean_name']:<12} "
            f"{r['price']:>14,.0f}  "
            f"{r['range_pct']:>7.1f}%  "
            f"{change_sign}{r['change_pct']:>6.1f}%  "
            f"{r['volume_billion']:>10.1f}B"
        )
    print(f"{'='*72}")


# ── 인터랙티브 선택 ─────────────────────────────────────────────────────────

def _interactive_select(results: list[dict]) -> list[str] | None:
    """
    사용자에게 번호를 입력받아 선택된 마켓 코드 목록을 반환한다.
    여러 개 선택 가능 (쉼표 구분).
    """
    print("\n번호를 입력하면 .env의 TARGET_MARKETS가 업데이트됩니다.")
    print("  예) 1        → 1번 코인으로 교체")
    print("  예) 1,3,5    → 세 코인 동시 감시")
    print("  예) Enter    → 변경 없이 종료\n")

    raw = input("선택 > ").strip()
    if not raw:
        print("변경 없이 종료합니다.")
        return None

    selected = []
    for part in raw.split(","):
        part = part.strip()
        if not part.isdigit():
            print(f"  잘못된 입력: '{part}' — 숫자만 입력하세요.")
            return None
        idx = int(part) - 1
        if idx < 0 or idx >= len(results):
            print(f"  범위 초과: {part} (1~{len(results)} 사이)")
            return None
        selected.append(results[idx]["market"])

    return selected


# ── 메인 ────────────────────────────────────────────────────────────────────

async def main() -> None:
    parser = argparse.ArgumentParser(description="업비트 변동성 코인 스캐너")
    parser.add_argument(
        "--min-range", type=float, default=15.0,
        metavar="PCT",
        help="24시간 고저 범위 최솟값 %% (기본: 15.0)",
    )
    parser.add_argument(
        "--min-volume", type=float, default=10.0,
        metavar="BILLION",
        help="24시간 최소 거래대금 단위: 10억 KRW (기본: 10.0 = 100억)",
    )
    parser.add_argument(
        "--top", type=int, default=20,
        help="최대 표시 개수 (기본: 20)",
    )
    parser.add_argument(
        "--no-select", action="store_true",
        help="결과만 출력하고 선택 단계 생략",
    )
    args = parser.parse_args()

    print(f"스캔 중... (조건: 24h 범위 ≥ {args.min_range}%,"
          f" 거래대금 ≥ {args.min_volume * 10:.0f}억 KRW)")

    results = await scan(
        min_range_pct=args.min_range,
        min_volume_billion=args.min_volume,
        top=args.top,
    )

    if not results:
        print("조건에 맞는 코인이 없습니다. --min-range 또는 --min-volume을 낮춰보세요.")
        return

    _print_table(results, args.min_range, args.min_volume)

    if args.no_select:
        return

    selected = _interactive_select(results)
    if selected is None:
        return

    _update_env_markets(selected)

    print(f"\n.env 업데이트 완료:")
    print(f"  TARGET_MARKETS={','.join(selected)}")
    print(f"\n트레이더를 시작하려면:")
    print(f"  python -m src.core.trader")


if __name__ == "__main__":
    asyncio.run(main())
