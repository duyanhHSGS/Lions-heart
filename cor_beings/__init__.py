"""Lions-heart product Beings and current examples."""

from cor_being import Being

from .agent_loop import AgentLoopBeing
from .approval import ApprovalBeing
from .auth import AuthBeing
from .bash import BashBeing
from .cli import CliBeing
from .edit import EditBeing
from .lion import LionBeing
from .model_gateway import ModelGatewayBeing
from .prompt import PromptBeing
from .projects import ProjectsBeing
from .providers import (
    AnthropicProviderBeing,
    GeminiProviderBeing,
    OpenAIProviderBeing,
    ProviderRegistryBeing,
)
from .read import ReadBeing
from .session import SessionBeing
from .settings import SettingsBeing
from .storage import StorageBeing
from .tool_shelf import ToolShelfBeing
from .turn_manager import TurnManagerBeing
from .web_ui import WebUiBeing
from .workspace import WorkspaceBeing


# TODO: Grow composition only by adding ordinary product Beings; keep the harness spine replaceable.
def get_beings() -> tuple[type[Being], ...]:
    """Return the first tiny Lions-heart harness in deterministic dependency order."""
    return (
        StorageBeing,
        SettingsBeing,
        AuthBeing,
        SessionBeing,
        OpenAIProviderBeing,
        AnthropicProviderBeing,
        GeminiProviderBeing,
        ProviderRegistryBeing,
        ModelGatewayBeing,
        WorkspaceBeing,
        ProjectsBeing,
        ReadBeing,
        EditBeing,
        BashBeing,
        ToolShelfBeing,
        ApprovalBeing,
        PromptBeing,
        AgentLoopBeing,
        TurnManagerBeing,
        WebUiBeing,
        CliBeing,
    )


__all__ = [
    "AgentLoopBeing",
    "AnthropicProviderBeing",
    "ApprovalBeing",
    "AuthBeing",
    "BashBeing",
    "CliBeing",
    "EditBeing",
    "GeminiProviderBeing",
    "LionBeing",
    "ModelGatewayBeing",
    "OpenAIProviderBeing",
    "PromptBeing",
    "ProjectsBeing",
    "ProviderRegistryBeing",
    "ReadBeing",
    "SessionBeing",
    "SettingsBeing",
    "StorageBeing",
    "ToolShelfBeing",
    "TurnManagerBeing",
    "WebUiBeing",
    "WorkspaceBeing",
    "get_beings",
]
