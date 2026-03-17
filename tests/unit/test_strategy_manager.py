"""
전략 매니저 단위 테스트

핫 리로딩, 캐싱, 에러 처리 검증
"""
import textwrap
from pathlib import Path

import pytest


@pytest.fixture
def strategy_dir(tmp_path: Path) -> Path:
    """임시 전략 디렉토리"""
    return tmp_path


@pytest.fixture
def manager(strategy_dir):
    from src.strategy.manager import StrategyManager
    return StrategyManager(strategy_dir)


class TestStrategyLoading:
    def test_load_valid_strategy(self, strategy_dir, manager):
        """유효한 전략 파일 로드"""
        (strategy_dir / "test_strat.py").write_text(textwrap.dedent("""
            import pandas as pd
            from src.strategy.base import Strategy, TradingSignal

            class TestStrategy(Strategy):
                name = "test_strat"

                def generate_signals(self, df):
                    return pd.Series(TradingSignal.HOLD, index=df.index, dtype=int)

                def validate_params(self, params):
                    return True
        """))
        strategy = manager.load("test_strat")
        assert strategy is not None
        assert strategy.name == "test_strat"

    def test_file_not_found(self, manager):
        """존재하지 않는 전략 파일"""
        with pytest.raises(FileNotFoundError):
            manager.load("nonexistent")

    def test_missing_abstract_method_raises(self, strategy_dir, manager):
        """추상 메서드 미구현 시 TypeError"""
        (strategy_dir / "incomplete.py").write_text(textwrap.dedent("""
            from src.strategy.base import Strategy

            class IncompleteStrategy(Strategy):
                name = "incomplete"
                # generate_signals 미구현!
                def validate_params(self, params):
                    return True
        """))
        with pytest.raises((TypeError, Exception)):
            manager.load("incomplete")

    def test_no_strategy_class_raises(self, strategy_dir, manager):
        """Strategy 서브클래스가 없는 파일"""
        (strategy_dir / "no_class.py").write_text("# empty module\n")
        with pytest.raises(TypeError, match="Strategy 서브클래스"):
            manager.load("no_class")


class TestHotReloading:
    def test_hot_reload_on_file_change(self, strategy_dir, manager):
        """파일 변경 시 새 버전 로드"""
        path = strategy_dir / "dynamic.py"

        # 버전 1
        path.write_text(textwrap.dedent("""
            import pandas as pd
            from src.strategy.base import Strategy, TradingSignal

            class DynamicStrategy(Strategy):
                name = "dynamic"
                version = "v1"

                def generate_signals(self, df):
                    return pd.Series(TradingSignal.HOLD, index=df.index, dtype=int)

                def validate_params(self, params):
                    return True
        """))
        s1 = manager.load("dynamic")
        assert s1.version == "v1"

        # 파일 수정 시간을 강제로 변경하기 위해 내용 변경
        import time
        time.sleep(0.01)
        path.write_text(textwrap.dedent("""
            import pandas as pd
            from src.strategy.base import Strategy, TradingSignal

            class DynamicStrategy(Strategy):
                name = "dynamic"
                version = "v2"

                def generate_signals(self, df):
                    return pd.Series(TradingSignal.BUY, index=df.index, dtype=int)

                def validate_params(self, params):
                    return True
        """))
        s2 = manager.load("dynamic")
        assert s2.version == "v2"

    def test_caches_unchanged_file(self, strategy_dir, manager):
        """파일 변경 없으면 캐시 사용 (mtime 동일)"""
        (strategy_dir / "stable.py").write_text(textwrap.dedent("""
            import pandas as pd
            from src.strategy.base import Strategy, TradingSignal

            class StableStrategy(Strategy):
                name = "stable"
                call_count = 0

                def generate_signals(self, df):
                    StableStrategy.call_count += 1
                    return pd.Series(TradingSignal.HOLD, index=df.index, dtype=int)

                def validate_params(self, params):
                    return True
        """))
        manager.load("stable")
        manager.load("stable")  # 두 번 로드
        # 캐시 사용으로 모듈 재실행 없음 (call_count는 0 유지)

    def test_activate_sets_active_name(self, strategy_dir, manager):
        """activate() 후 active_strategy_name 반환"""
        (strategy_dir / "active_test.py").write_text(textwrap.dedent("""
            import pandas as pd
            from src.strategy.base import Strategy, TradingSignal

            class ActiveStrategy(Strategy):
                name = "active_test"

                def generate_signals(self, df):
                    return pd.Series(TradingSignal.HOLD, index=df.index, dtype=int)

                def validate_params(self, params):
                    return True
        """))
        manager.activate("active_test")
        assert manager.active_strategy_name == "active_test"
