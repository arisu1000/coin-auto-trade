"""가상 포트폴리오 상태 추적"""


class Portfolio:
    """
    백테스트용 포트폴리오

    진입/청산 시 현금과 코인 수량을 갱신하고 PnL을 추적한다.
    """

    def __init__(self, initial_capital: float) -> None:
        self._cash = initial_capital
        self._position_size: float = 0.0   # 보유 코인 수량
        self._avg_price: float = 0.0

    @property
    def has_position(self) -> bool:
        return self._position_size > 0

    @property
    def position_size(self) -> float:
        return self._position_size

    @property
    def cash(self) -> float:
        return self._cash

    def max_quantity(self, price: float) -> float:
        """현재 현금으로 매수 가능한 최대 수량"""
        if price <= 0:
            return 0.0
        return self._cash / price

    def enter_long(self, price: float, quantity: float, fee: float) -> None:
        """매수 진입"""
        cost = price * quantity + fee
        if cost > self._cash:
            # 수수료 포함 최대 수량으로 조정
            quantity = (self._cash - fee) / price
            cost = self._cash
        self._cash -= cost
        self._position_size = quantity
        self._avg_price = price

    def exit_long(self, price: float, fee: float) -> float:
        """매도 청산 (PnL 반환)"""
        gross = price * self._position_size
        pnl = gross - fee - (self._avg_price * self._position_size)
        self._cash += gross - fee
        self._position_size = 0.0
        self._avg_price = 0.0
        return pnl

    def total_equity(self, current_price: float) -> float:
        """총 자산 평가액 (현금 + 코인 평가액)"""
        return self._cash + self._position_size * current_price
