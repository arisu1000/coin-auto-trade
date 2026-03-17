"""
LangGraph 워크플로우 조립

노드 실행 순서: bull → bear → judge → END
SqliteCheckpointer로 상태를 영속화하여 재시작 후에도 맥락 유지
"""
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph

from src.agents.bear_agent import bear_node
from src.agents.bull_agent import bull_node
from src.agents.judge_agent import judge_node
from src.agents.state import AgentState
from src.config.settings import Settings


def build_workflow(settings: Settings, db_path: str | None = None):
    """
    LangGraph StateGraph 빌드 및 컴파일

    Args:
        settings: 앱 설정
        db_path: SQLite 경로 (None이면 인메모리)

    Returns:
        CompiledGraph 인스턴스
    """
    llm = ChatOpenAI(
        model=settings.llm_model,
        temperature=settings.llm_temperature,
        api_key=settings.openai_api_key.get_secret_value(),
    )

    # 클로저로 LLM 인스턴스 바인딩 (DI 패턴)
    async def _bull(state: AgentState) -> dict:
        return await bull_node(state, llm=llm)

    async def _bear(state: AgentState) -> dict:
        return await bear_node(state, llm=llm)

    async def _judge(state: AgentState) -> dict:
        return await judge_node(state, llm=llm)

    # 그래프 구성
    builder = StateGraph(AgentState)
    builder.add_node("bull", _bull)
    builder.add_node("bear", _bear)
    builder.add_node("judge", _judge)

    builder.set_entry_point("bull")
    builder.add_edge("bull", "bear")
    builder.add_edge("bear", "judge")
    builder.add_edge("judge", END)

    # SqliteCheckpointer 설정 (상태 영속화)
    checkpointer = None
    if db_path:
        try:
            from langgraph.checkpoint.sqlite import SqliteSaver
            checkpointer = SqliteSaver.from_conn_string(db_path)
        except ImportError:
            # langgraph.checkpoint.sqlite가 없을 경우 메모리 체크포인터 사용
            from langgraph.checkpoint.memory import MemorySaver
            checkpointer = MemorySaver()

    if checkpointer:
        return builder.compile(checkpointer=checkpointer)
    return builder.compile()
