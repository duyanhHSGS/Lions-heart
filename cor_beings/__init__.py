"""Lions-heart product Beings and current examples."""

from cor_being import Being

from .agent_loop import AgentLoopBeing
from .bash import BashBeing
from .cli import CliBeing
from .edit import EditBeing
from .lion import LionBeing
from .prompt import PromptBeing
from .read import ReadBeing
from .session import SessionBeing
from .tool_shelf import ToolShelfBeing


# TODO: Grow composition only by adding ordinary product Beings; keep the harness spine replaceable.
def get_beings() -> tuple[type[Being], ...]:
    """Return the first tiny Lions-heart harness in deterministic dependency order."""
    return (
        SessionBeing,
        LionBeing,
        ReadBeing,
        EditBeing,
        BashBeing,
        ToolShelfBeing,
        PromptBeing,
        AgentLoopBeing,
        CliBeing,
    )


__all__ = [
    "AgentLoopBeing",
    "BashBeing",
    "CliBeing",
    "EditBeing",
    "LionBeing",
    "PromptBeing",
    "ReadBeing",
    "SessionBeing",
    "ToolShelfBeing",
    "get_beings",
]
