"""
백테스트 성과 지표 계산

샤프 지수, MDD, 승률, 수익 팩터 등 표준 퀀트 지표를 산출한다.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.backtest.engine import TradeRecord


@dataclass
class BacktestReport:
    equity_curve: list[float]
    trade_records: list["TradeRecord"]
    initial_capital: float

    def total_return_pct(self) -> float:
        """총 수익률 (%)"""
        if not self.equity_curve or self.initial_capital == 0:
            return 0.0
        return (self.equity_curve[-1] - self.initial_capital) / self.initial_capital * 100

    def max_drawdown_pct(self) -> float:
        """
        최대 낙폭 (%)

        MDD = (Peak - Trough) / Peak * 100
        """
        if not self.equity_curve:
            return 0.0

        peak = self.equity_curve[0]
        mdd = 0.0
        for equity in self.equity_curve:
            if equity > peak:
                peak = equity
            dd = (peak - equity) / peak * 100 if peak > 0 else 0.0
            if dd > mdd:
                mdd = dd
        return mdd

    def sharpe_ratio(self, risk_free_rate: float = 0.0, periods_per_year: int = 252) -> float:
        """
        샤프 지수

        S = (R_p - R_f) / σ_p
        일별 수익률 기준으로 연환산
        """
        if len(self.equity_curve) < 2:
            return 0.0

        returns = [
            (self.equity_curve[i] - self.equity_curve[i - 1]) / self.equity_curve[i - 1]
            for i in range(1, len(self.equity_curve))
        ]

        n = len(returns)
        if n == 0:
            return 0.0

        mean_ret = sum(returns) / n
        variance = sum((r - mean_ret) ** 2 for r in returns) / n
        std_ret = math.sqrt(variance)

        if std_ret == 0:
            return 0.0

        daily_rf = risk_free_rate / periods_per_year
        return (mean_ret - daily_rf) / std_ret * math.sqrt(periods_per_year)

    def win_rate(self) -> float:
        """승률 (%)"""
        if not self.trade_records:
            return 0.0
        wins = sum(1 for t in self.trade_records if t.pnl > 0)
        return wins / len(self.trade_records) * 100

    def profit_factor(self) -> float:
        """
        수익 팩터 = 총수익 / 총손실

        1.5 이상이면 안정적인 엣지 보유로 판단
        """
        gross_profit = sum(t.pnl for t in self.trade_records if t.pnl > 0)
        gross_loss = abs(sum(t.pnl for t in self.trade_records if t.pnl < 0))
        if gross_loss == 0:
            return float("inf") if gross_profit > 0 else 0.0
        return gross_profit / gross_loss

    def calmar_ratio(self) -> float:
        """칼마 지수 = 연간 수익률 / MDD"""
        mdd = self.max_drawdown_pct()
        if mdd == 0:
            return 0.0
        return self.total_return_pct() / mdd

    def total_trades(self) -> int:
        return len(self.trade_records)

    def avg_pnl_per_trade(self) -> float:
        if not self.trade_records:
            return 0.0
        return sum(t.pnl for t in self.trade_records) / len(self.trade_records)

    def summary(self) -> dict:
        """주요 지표 요약 딕셔너리"""
        return {
            "total_return_pct": round(self.total_return_pct(), 2),
            "max_drawdown_pct": round(self.max_drawdown_pct(), 2),
            "sharpe_ratio": round(self.sharpe_ratio(), 3),
            "calmar_ratio": round(self.calmar_ratio(), 3),
            "win_rate_pct": round(self.win_rate(), 2),
            "profit_factor": round(self.profit_factor(), 3),
            "total_trades": self.total_trades(),
            "avg_pnl_per_trade": round(self.avg_pnl_per_trade(), 2),
            "final_capital": round(self.equity_curve[-1] if self.equity_curve else 0, 0),
        }

    def side_breakdown(self) -> dict:
        """롱/숏별 거래 통계"""
        result = {}
        for side in ("long", "short"):
            trades = [t for t in self.trade_records if t.side == side]
            if not trades:
                continue
            wins = sum(1 for t in trades if t.pnl > 0)
            total_pnl = sum(t.pnl for t in trades)
            gross_profit = sum(t.pnl for t in trades if t.pnl > 0)
            gross_loss = abs(sum(t.pnl for t in trades if t.pnl < 0))
            result[side] = {
                "trades": len(trades),
                "win_rate_pct": round(wins / len(trades) * 100, 1),
                "total_pnl": round(total_pnl, 0),
                "profit_factor": round(gross_profit / gross_loss, 3) if gross_loss else float("inf"),
            }
        return result

    def summary_text(self) -> str:
        """텍스트 요약"""
        s = self.summary()
        lines = [
            "백테스트 결과",
            f"총 수익률: {s['total_return_pct']:+.2f}%",
            f"최대 낙폭(MDD): {s['max_drawdown_pct']:.2f}%",
            f"샤프 지수: {s['sharpe_ratio']:.3f}",
            f"수익 팩터: {s['profit_factor']:.3f}",
            f"승률: {s['win_rate_pct']:.1f}%",
            f"총 거래 횟수: {s['total_trades']}",
            f"최종 자산: {s['final_capital']:,.0f}원",
        ]
        breakdown = self.side_breakdown()
        if len(breakdown) > 1:
            lines.append("")
            lines.append("[포지션별 분석]")
            for side, stat in breakdown.items():
                label = "롱" if side == "long" else "숏"
                lines.append(
                    f"  {label}: {stat['trades']}회 | "
                    f"승률 {stat['win_rate_pct']}% | "
                    f"수익팩터 {stat['profit_factor']} | "
                    f"손익 {stat['total_pnl']:+,.0f}원"
                )
        return "\n".join(lines)
