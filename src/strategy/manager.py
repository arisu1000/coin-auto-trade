"""
핫 리로딩 전략 매니저

importlib.util.spec_from_file_location 을 사용하여
Python 모듈 캐시(sys.modules)를 완전히 우회하고
파일 변경 시 서버 재시작 없이 즉각 반영한다.
"""
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import structlog

from src.strategy.base import Strategy

logger = structlog.get_logger(__name__)


class StrategyManager:
    """
    전략 파일의 변경을 감지하고 런타임에 교체한다.

    사용법:
        manager = StrategyManager(Path("src/strategy"))
        strategy = manager.load("momentum")  # 최초 로드
        # 파일 수정 후
        strategy = manager.load("momentum")  # 자동으로 새 버전 로드
    """

    def __init__(self, strategy_dir: Path) -> None:
        self._dir = strategy_dir
        self._modules: dict[str, ModuleType] = {}
        self._classes: dict[str, type[Strategy]] = {}
        self._mtimes: dict[str, float] = {}
        self._active_name: str | None = None

    @property
    def active_strategy_name(self) -> str | None:
        return self._active_name

    def load(self, name: str) -> Strategy:
        """
        전략 이름으로 인스턴스를 반환한다.

        파일 수정 시간(mtime)이 변경된 경우 모듈을 재로드한다.
        """
        module_path = self._dir / f"{name}.py"
        if not module_path.exists():
            raise FileNotFoundError(f"전략 파일을 찾을 수 없음: {module_path}")

        current_mtime = module_path.stat().st_mtime
        cached_mtime = self._mtimes.get(name, -1)

        if name in self._classes and current_mtime == cached_mtime:
            # 캐시된 버전 사용
            return self._classes[name]()

        # 재로드: sys.modules에서 이전 모듈 완전 제거
        module_key = f"strategy._hot_{name}"
        if module_key in sys.modules:
            del sys.modules[module_key]

        spec = importlib.util.spec_from_file_location(module_key, module_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"전략 모듈 스펙 생성 실패: {module_path}")

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # type: ignore[union-attr]

        # 모듈에서 Strategy 서브클래스 탐색
        strategy_class = self._find_strategy_class(module, name)
        if strategy_class is None:
            raise TypeError(
                f"'{module_path}'에서 Strategy 서브클래스를 찾을 수 없습니다. "
                "Strategy를 상속받은 클래스가 있어야 합니다."
            )

        self._modules[name] = module
        self._classes[name] = strategy_class
        self._mtimes[name] = current_mtime

        if cached_mtime > 0:
            logger.info("strategy_hot_reloaded", name=name, path=str(module_path))
        else:
            logger.info("strategy_loaded", name=name, path=str(module_path))

        return strategy_class()

    def activate(self, name: str) -> Strategy:
        """전략을 로드하고 현재 활성 전략으로 설정"""
        strategy = self.load(name)
        self._active_name = name
        logger.info("strategy_activated", name=name)
        return strategy

    def get_active(self) -> Strategy | None:
        """현재 활성화된 전략 인스턴스 반환 (없으면 None)"""
        if self._active_name is None:
            return None
        return self.load(self._active_name)

    def list_available(self) -> list[str]:
        """사용 가능한 전략 파일 목록"""
        return [f.stem for f in self._dir.glob("*.py")
                if not f.stem.startswith("_") and f.stem not in ("base", "manager")]

    @staticmethod
    def _find_strategy_class(module: ModuleType, name: str) -> type[Strategy] | None:
        """모듈에서 Strategy 서브클래스를 찾아 반환"""
        for attr_name in dir(module):
            obj = getattr(module, attr_name)
            if (
                isinstance(obj, type)
                and issubclass(obj, Strategy)
                and obj is not Strategy
            ):
                return obj
        return None
