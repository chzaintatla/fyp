"""Central registry of all tools the AI is allowed to invoke."""
from __future__ import annotations

import json

from .packages import package_tools
from .messaging import messaging_tools
from .apps import app_tools
from .base import BaseTool, ToolContext
from .desktop import DesktopControlTool
from .browser import BrowserControlTool
from .filesystem import filesystem_tools
from .media import media_tools
from .system import system_tools
from .web import web_tools
from .memory import memory_tools
from .knowledge import knowledge_tools
from .uia import uia_tools
from .terminal import terminal_tools
from .mail import mail_tools
from .messenger import messenger_tools


class UnknownToolError(ValueError):
    """Raised when the AI (or a caller) requests a tool that is not registered."""


class ToolRegistry:
    def __init__(self, ctx: ToolContext | None = None) -> None:
        self.ctx = ctx or ToolContext.from_settings()
        tools: list[BaseTool] = []
        tools.extend(filesystem_tools(self.ctx))
        tools.extend(app_tools(self.ctx))
        tools.extend(package_tools(self.ctx))
        tools.extend(messaging_tools(self.ctx))
        tools.append(DesktopControlTool(self.ctx))
        tools.append(BrowserControlTool(self.ctx))
        tools.extend(system_tools(self.ctx))
        tools.extend(web_tools(self.ctx))
        tools.extend(media_tools(self.ctx))
        tools.extend(memory_tools(self.ctx))
        tools.extend(knowledge_tools(self.ctx))
        tools.extend(uia_tools(self.ctx))
        tools.extend(terminal_tools(self.ctx))
        tools.extend(mail_tools(self.ctx))
        tools.extend(messenger_tools(self.ctx))
        self._tools: dict[str, BaseTool] = {t.name: t for t in tools}

    def get(self, name: str) -> BaseTool:
        try:
            return self._tools[name]
        except KeyError:
            raise UnknownToolError(
                f"Unknown tool '{name}'. Available tools: {', '.join(sorted(self._tools))}"
            ) from None

    def names(self) -> list[str]:
        return sorted(self._tools)

    def model_names(self) -> list[str]:
        """The tools the model may use: every tool except the ones only server code may run (send_email)."""
        return [n for n in self.names() if not self._tools[n].hidden_from_model]

    def metadata(self) -> list[dict]:
        return [self._tools[n].describe() for n in self.model_names()]

    def describe_for_prompt(self) -> str:
        """Compact tool catalogue for the AI system prompt."""
        lines = []
        for name in self.model_names():
            tool = self._tools[name]
            schema = tool.args_schema.model_json_schema()
            props = json.dumps(schema.get("properties", {}), separators=(",", ":"))
            lines.append(
                f"- {name}: {tool.description} "
                f"(risk: {tool.risk_level.value}, args: {props})"
            )
        return "\n".join(lines)
