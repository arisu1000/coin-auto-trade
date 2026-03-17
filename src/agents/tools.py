"""
LangChain 도구 모음 - 에이전트가 호출 가능한 시장 데이터 조회 함수
"""
from langchain_core.tools import tool


@tool
def get_fear_greed_index() -> dict:
    """암호화폐 공포/탐욕 지수를 조회합니다 (0=극도 공포, 100=극도 탐욕)"""
    # alternative.me API 활용 (실제 구현 시 aiohttp 비동기 호출로 대체)
    return {
        "value": 50,
        "classification": "Neutral",
        "timestamp": "2024-01-01",
    }


@tool
def analyze_volume_profile(
    volume_24h: float,
    avg_volume_7d: float,
) -> dict:
    """
    거래량 프로파일을 분석합니다.

    Args:
        volume_24h: 24시간 거래량
        avg_volume_7d: 7일 평균 거래량

    Returns:
        volume_surge: 거래량 급증 여부
        surge_ratio: 급증 비율 (24h/7d평균)
    """
    ratio = volume_24h / avg_volume_7d if avg_volume_7d > 0 else 1.0
    return {
        "volume_surge": ratio > 2.0,
        "surge_ratio": round(ratio, 2),
        "interpretation": (
            "강한 거래량 급증 - 추세 전환 가능성" if ratio > 2.0
            else "정상 거래량 범위"
        ),
    }


@tool
def calculate_support_resistance(
    highs: list[float],
    lows: list[float],
    current_price: float,
) -> dict:
    """
    최근 고가/저가 기반으로 지지/저항 수준을 계산합니다.

    Args:
        highs: 최근 N봉 고가 리스트
        lows: 최근 N봉 저가 리스트
        current_price: 현재가

    Returns:
        nearest_resistance: 가장 가까운 위 저항선
        nearest_support: 가장 가까운 아래 지지선
        distance_to_resistance_pct: 저항까지 거리 (%)
        distance_to_support_pct: 지지까지 거리 (%)
    """
    resistances = [h for h in highs if h > current_price]
    supports = [l for l in lows if l < current_price]

    nearest_resistance = min(resistances) if resistances else current_price * 1.05
    nearest_support = max(supports) if supports else current_price * 0.95

    return {
        "nearest_resistance": nearest_resistance,
        "nearest_support": nearest_support,
        "distance_to_resistance_pct": round(
            (nearest_resistance - current_price) / current_price * 100, 2
        ),
        "distance_to_support_pct": round(
            (current_price - nearest_support) / current_price * 100, 2
        ),
    }
