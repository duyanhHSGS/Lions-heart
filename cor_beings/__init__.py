"""Lions-heart product Beings and current examples."""

from cor_being import Being

from .agent_loop import AgentLoopBeing
from .activity import ActivityBeing
from .approval import ApprovalBeing
from .attachments import AttachmentBeing
from .audio import AudioBeing
from .auth import AuthBeing
from .bash import BashBeing
from .cli import CliBeing
from .edit import EditBeing
from .lion import LionBeing
from .images import ImageBeing
from .media_jobs import MediaJobBeing
from .model_gateway import ModelGatewayBeing
from .mcp import McpBeing
from .prompt import PromptBeing
from .projects import ProjectsBeing
from .providers import (
    AnthropicProviderBeing,
    GeminiProviderBeing,
    OpenAIProviderBeing,
    ProviderRegistryBeing,
)
from .read import ReadBeing
from .recipes import RecipeBeing
from .saved_prompts import SavedPromptsBeing
from .session import SessionBeing
from .settings import SettingsBeing
from .storage import StorageBeing
from .tool_shelf import ToolShelfBeing
from .turn_manager import TurnManagerBeing
from .video import VideoBeing
from .web_ui import WebUiBeing
from .workspace import WorkspaceBeing


# TODO: Grow composition only by adding ordinary product Beings; keep the harness spine replaceable.
def get_beings() -> tuple[type[Being], ...]:
    """Return the first tiny Lions-heart harness in deterministic dependency order."""
    return (
        StorageBeing,
        ActivityBeing,
        SettingsBeing,
        AuthBeing,
        SessionBeing,
        AttachmentBeing,
        OpenAIProviderBeing,
        AnthropicProviderBeing,
        GeminiProviderBeing,
        ProviderRegistryBeing,
        ModelGatewayBeing,
        WorkspaceBeing,
        ProjectsBeing,
        SavedPromptsBeing,
        ReadBeing,
        EditBeing,
        BashBeing,
        ToolShelfBeing,
        McpBeing,
        MediaJobBeing,
        ImageBeing,
        AudioBeing,
        VideoBeing,
        RecipeBeing,
        ApprovalBeing,
        PromptBeing,
        AgentLoopBeing,
        TurnManagerBeing,
        WebUiBeing,
        CliBeing,
    )


__all__ = [
    "AgentLoopBeing",
    "ActivityBeing",
    "AnthropicProviderBeing",
    "ApprovalBeing",
    "AttachmentBeing",
    "AudioBeing",
    "AuthBeing",
    "BashBeing",
    "CliBeing",
    "EditBeing",
    "GeminiProviderBeing",
    "LionBeing",
    "ImageBeing",
    "MediaJobBeing",
    "ModelGatewayBeing",
    "McpBeing",
    "OpenAIProviderBeing",
    "PromptBeing",
    "ProjectsBeing",
    "ProviderRegistryBeing",
    "ReadBeing",
    "RecipeBeing",
    "SavedPromptsBeing",
    "SessionBeing",
    "SettingsBeing",
    "StorageBeing",
    "ToolShelfBeing",
    "TurnManagerBeing",
    "VideoBeing",
    "WebUiBeing",
    "WorkspaceBeing",
    "get_beings",
]
