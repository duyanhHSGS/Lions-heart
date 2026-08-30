"""Provider-neutral model -> tools -> model driver for Lions-heart."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace
from threading import Event, Lock
from uuid import uuid4

from cor_being import Being, Life, World
from cor_beings.approval import ApprovalBeing
from cor_beings.attachments import AttachmentBeing
from cor_beings.lion import ModelReply, ToolCall
from cor_beings.model_gateway import ModelGatewayBeing
from cor_beings.prompt import PromptBeing
from cor_beings.providers import ProviderEvent
from cor_beings.session import SessionBeing
from cor_beings.tool_shelf import ToolShelfBeing


class AgentLoopBeing(Being):
    """Drive one complete user turn without knowing concrete tool internals."""

    name = "agent_loop"
    needs = (
        SessionBeing,
        PromptBeing,
        ToolShelfBeing,
        ModelGatewayBeing,
        ApprovalBeing,
    )

    def __init__(self) -> None:
        self._session: SessionBeing | None = None
        self._prompt: PromptBeing | None = None
        self._tools: ToolShelfBeing | None = None
        self._gateway: ModelGatewayBeing | None = None
        self._approval: ApprovalBeing | None = None
        self._attachments: AttachmentBeing | None = None
        self._locks_guard = Lock()
        self._conversation_locks: dict[str, Lock] = {}

    def birth(self, world: World, life: Life) -> None:
        session = world.need(SessionBeing)
        prompt = world.need(PromptBeing)
        tools = world.need(ToolShelfBeing)
        gateway = world.need(ModelGatewayBeing)
        approval = world.need(ApprovalBeing)
        try:
            attachments = world.need(AttachmentBeing)
        except LookupError:
            attachments = None
        life.on_death(self._forget_dependencies)
        self._session = session
        self._prompt = prompt
        self._tools = tools
        self._gateway = gateway
        self._approval = approval
        self._attachments = attachments
        # TODO: Evict idle per-conversation locks after very large chat churn.

    def _forget_dependencies(self) -> None:
        self._session = None
        self._prompt = None
        self._tools = None
        self._gateway = None
        self._approval = None
        self._attachments = None
        with self._locks_guard:
            self._conversation_locks.clear()

    def run_turn(self, message: str, *, max_steps: int = 16) -> str:
        if not isinstance(message, str):
            raise TypeError("message must be a string")
        if (
            not isinstance(max_steps, int)
            or isinstance(max_steps, bool)
            or max_steps <= 0
        ):
            raise ValueError("max_steps must be a positive integer")
        conversation_id = self._active_conversation_id()
        with self._lock_for(conversation_id):
            return self._run_serial_turn(
                message,
                max_steps=max_steps,
                turn_id=f"blocking-{uuid4().hex}",
                cancel=Event(),
                conversation_id=conversation_id,
            )

    def _run_serial_turn(
        self,
        message: str,
        *,
        max_steps: int,
        turn_id: str,
        cancel: Event,
        conversation_id: str,
        emit: Callable[[ProviderEvent], None] | None = None,
    ) -> str:
        """Run one turn while preventing CLI/web history interleaving."""
        if (
            self._session is None
            or self._prompt is None
            or self._tools is None
            or self._gateway is None
            or self._approval is None
        ):
            raise RuntimeError("agent loop is not alive")

        attachment_ids: tuple[str, ...] = ()
        if self._attachments is not None:
            attachment_ids = tuple(
                str(item["id"])
                for item in self._attachments.list(conversation_id=conversation_id)
            )
        event_data: dict[str, object] = {"text": message}
        if attachment_ids:
            event_data["attachment_ids"] = attachment_ids
        self._session.append_to(conversation_id, "user", **event_data)

        for _ in range(max_steps):
            if cancel.is_set():
                self._session.append_to(
                    conversation_id, "agent_cancelled", turn_id=turn_id
                )
                return ""
            reply = self._remote_reply(conversation_id, cancel=cancel, emit=emit)
            self._session.append_to(
                conversation_id,
                "assistant",
                text=reply.text,
                tool_calls=reply.tool_calls,
            )

            if not reply.tool_calls:
                return reply.text

            for call in reply.tool_calls:
                try:
                    approval_id = self._approval.create(
                        turn_id, call.name, call.arguments
                    )
                    if emit is not None:
                        emit(
                            ProviderEvent.make(
                                "approval_required",
                                approval_id=approval_id,
                                tool=call.name,
                                arguments=dict(call.arguments),
                            )
                        )
                    decision = self._approval.wait_and_execute(
                        approval_id, cancel=cancel
                    )
                except Exception as error:
                    self._session.append_to(
                        conversation_id,
                        "tool_error",
                        name=call.name,
                        error=type(error).__name__,
                        message=str(error),
                    )
                    raise
                if not decision.approved:
                    self._session.append_to(
                        conversation_id,
                        "tool_result",
                        name=call.name,
                        denied=True,
                        result=decision.result,
                    )
                    if emit is not None:
                        emit(
                            ProviderEvent.make(
                                "tool_result",
                                approval_id=approval_id,
                                tool=call.name,
                                denied=True,
                                result=decision.result,
                            )
                        )
                    continue
                result = decision.result
                self._session.append_to(
                    conversation_id,
                    "tool_result",
                    name=call.name,
                    result=result,
                )
                if emit is not None:
                    emit(
                        ProviderEvent.make(
                            "tool_result",
                            approval_id=approval_id,
                            tool=call.name,
                            denied=False,
                            result=result,
                        )
                    )

        self._session.append_to(
            conversation_id, "agent_error", error="step_limit", max_steps=max_steps
        )
        raise RuntimeError(f"agent turn exceeded {max_steps} model steps")

    def stream_turn(
        self,
        message: str,
        *,
        turn_id: str | None = None,
        cancel: Event | None = None,
        emit: Callable[[ProviderEvent], None] | None = None,
        conversation_id: str | None = None,
    ) -> tuple[ProviderEvent, ...]:
        """Run a remote turn while delivering normalized events incrementally."""
        if not isinstance(message, str) or not message.strip():
            raise ValueError("message must be a non-empty string")
        trace: list[ProviderEvent] = []

        def publish(event: ProviderEvent) -> None:
            trace.append(event)
            if emit is not None:
                emit(event)

        target_conversation = conversation_id or self._active_conversation_id()
        with self._lock_for(target_conversation):
            self._run_serial_turn(
                message,
                max_steps=16,
                turn_id=turn_id or uuid4().hex,
                cancel=cancel or Event(),
                emit=publish,
                conversation_id=target_conversation,
            )
        return tuple(trace)

    def _remote_reply(
        self,
        conversation_id: str,
        *,
        cancel: Event | None = None,
        trace: list[ProviderEvent] | None = None,
        emit: Callable[[ProviderEvent], None] | None = None,
    ) -> ModelReply:
        if self._gateway is None or self._session is None:
            raise RuntimeError("agent loop is not alive")
        messages: list[dict[str, object]] = []
        for event in self._session.events_for(conversation_id):
            if event.kind in ("user", "assistant"):
                messages.append(
                    {"role": event.kind, "content": str(event.data.get("text", ""))}
                )
            elif event.kind in ("tool_result", "tool_error"):
                messages.append(
                    {
                        "role": "user",
                        "content": json.dumps(dict(event.data), default=str),
                        "_lion_kind": event.kind,
                    }
                )
        request = self._gateway.default_request(tuple(messages))
        if self._attachments is not None:
            query = next(
                (str(event.data.get("text", "")) for event in reversed(self._session.events_for(conversation_id))
                 if event.kind == "user"),
                "",
            )
            snippets = self._attachments.context_for(query, conversation_id=conversation_id)
            if snippets:
                context = "\n\n".join(
                    f"Attachment {item['name']} ({item['id']}):\n{item['text']}" for item in snippets
                )
                request = replace(
                    request,
                    system=f"{request.system}\n\nUse these retrieved attachment excerpts when relevant:\n{context}".strip(),
                    attachments=tuple({"id": item["id"], "name": item["name"], "kind": "retrieved_text"}
                                      for item in snippets),
                )
        if self._tools is not None:
            request = replace(request, tools=self._tools.schemas)
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for provider_event in self._gateway.stream(request, cancel or Event()):
            if trace is not None:
                trace.append(provider_event)
            if emit is not None:
                emit(provider_event)
            if provider_event.kind == "text_delta":
                text_parts.append(str(provider_event.data.get("text", "")))
            elif provider_event.kind == "tool_call":
                raw = provider_event.data.get("arguments", "{}")
                try:
                    arguments = json.loads(str(raw))
                except json.JSONDecodeError as error:
                    raise RuntimeError(
                        "provider returned malformed tool arguments"
                    ) from error
                if not isinstance(arguments, dict):
                    raise RuntimeError("provider tool arguments must be an object")
                tool_calls.append(
                    ToolCall(str(provider_event.data.get("name", "")), arguments)
                )
        return ModelReply("".join(text_parts), tuple(tool_calls))

    def _active_conversation_id(self) -> str:
        if self._session is None:
            raise RuntimeError("agent loop is not alive")
        return self._session.conversation_id

    def _lock_for(self, conversation_id: str) -> Lock:
        with self._locks_guard:
            return self._conversation_locks.setdefault(conversation_id, Lock())
