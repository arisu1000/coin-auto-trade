"""수수료 스케줄"""


class FeeSchedule:
    """
    업비트 원화 마켓 수수료 (고정 0.05%)
    """

    def __init__(self, rate_bps: int = 5) -> None:
        self.rate = rate_bps / 10_000  # 0.0005

    def calculate(self, trade_value: float) -> float:
        """거래 금액에 대한 수수료 계산"""
        return trade_value * self.rate
