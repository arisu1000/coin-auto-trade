"""가상 포트폴리오 상태 추적 (롱/숏 지원)"""


class Portfolio:
    """
    백테스트용 포트폴리오

    진입/청산 시 현금과 코인 수량을 갱신하고 PnL을 추적한다.
    숏 진입 시 unit_amount를 증거금으로 예치(현금 차감),
    청산 시 증거금 + 손익을 돌려받는 1:1 마진 모델을 사용한다.
    """

    def __init__(self, initial_capital: float) -> None:
        self._cash = initial_capital
        # ── 롱 ──────────────────────────────────────────
        self._long_qty: float = 0.0
        self._long_avg_price: float = 0.0
        # ── 숏 ──────────────────────────────────────────
        self._short_qty: float = 0.0
        self._short_avg_price: float = 0.0

    # ── 속성 ────────────────────────────────────────────

    @property
    def cash(self) -> float:
        return self._cash

    @property
    def has_long_position(self) -> bool:
        return self._long_qty > 0

    @property
    def has_short_position(self) -> bool:
        return self._short_qty > 0

    @property
    def has_position(self) -> bool:
        """롱 또는 숏 포지션 보유 여부 (하위 호환)"""
        return self.has_long_position or self.has_short_position

    @property
    def long_qty(self) -> float:
        return self._long_qty

    @property
    def long_avg_price(self) -> float:
        return self._long_avg_price

    @property
    def short_qty(self) -> float:
        return self._short_qty

    @property
    def short_avg_price(self) -> float:
        return self._short_avg_price

    # 하위 호환용 alias
    @property
    def position_size(self) -> float:
        return self._long_qty

    # ── 롱 ──────────────────────────────────────────────

    def max_quantity(self, price: float) -> float:
        """현재 현금으로 매수 가능한 최대 수량"""
        if price <= 0:
            return 0.0
        return self._cash / price

    def enter_long(self, price: float, quantity: float, fee: float) -> None:
        """롱 진입 (피라미딩 시 가중평균 갱신)"""
        cost = price * quantity + fee
        if cost > self._cash:
            quantity = (self._cash - fee) / price
            cost = self._cash
        self._cash -= cost
        total = self._long_qty + quantity
        if total > 0:
            self._long_avg_price = (
                self._long_avg_price * self._long_qty + price * quantity
            ) / total
        self._long_qty = total

    def exit_long(self, price: float, fee: float) -> float:
        """롱 청산 (PnL 반환)"""
        gross = price * self._long_qty
        pnl = gross - fee - self._long_avg_price * self._long_qty
        self._cash += gross - fee
        self._long_qty = 0.0
        self._long_avg_price = 0.0
        return pnl

    # ── 숏 ──────────────────────────────────────────────

    def enter_short(self, price: float, quantity: float, fee: float) -> None:
        """
        숏 진입 (피라미딩 시 가중평균 갱신)

        1:1 마진 모델: price * quantity를 증거금으로 현금에서 차감.
        """
        margin = price * quantity + fee
        if margin > self._cash:
            quantity = (self._cash - fee) / price
            margin = self._cash
        self._cash -= margin
        total = self._short_qty + quantity
        if total > 0:
            self._short_avg_price = (
                self._short_avg_price * self._short_qty + price * quantity
            ) / total
        self._short_qty = total

    def exit_short(self, price: float, fee: float) -> float:
        """
        숏 청산 (PnL 반환)

        증거금 회수 + 손익 정산.
        """
        committed = self._short_avg_price * self._short_qty
        gross_pnl = (self._short_avg_price - price) * self._short_qty
        self._cash += committed + gross_pnl - fee
        pnl = gross_pnl - fee
        self._short_qty = 0.0
        self._short_avg_price = 0.0
        return pnl

    # ── 평가 ────────────────────────────────────────────

    def total_equity(self, current_price: float) -> float:
        """
        총 자산 평가액

        = 현금 + 롱 평가액 + 숏 미실현손익
        (현금은 숏 증거금이 이미 차감된 값)
        """
        long_value = self._long_qty * current_price
        short_unrealized = (
            (self._short_avg_price - current_price) * self._short_qty
        )
        return self._cash + long_value + short_unrealized
