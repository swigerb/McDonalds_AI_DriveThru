"""Abstract base class for audio/AI processors (cloud or local).

Defines the interface contract that both RTMiddleTier (cloud) and
LocalPhi4Processor (local/offline) must satisfy so the ProcessorRouter
can delegate WebSocket connections transparently.
"""

from abc import ABC, abstractmethod

from aiohttp import web

from rtmt import Tool


class AbstractProcessor(ABC):
    """Base class for audio/AI processors (cloud or local).

    Subclasses handle bidirectional WebSocket communication between the
    browser client and an AI backend (Azure OpenAI Realtime or local
    Phi-4 ONNX).  The ProcessorRouter selects which processor handles
    each incoming connection.
    """

    # Processor configuration — concrete classes populate these.
    tools: dict[str, Tool]
    system_message: str | None
    temperature: float | None
    max_tokens: int | None
    voice_choice: str | None

    @abstractmethod
    async def handle_websocket(self, ws: web.WebSocketResponse, request: web.Request) -> None:
        """Handle a WebSocket connection — bidirectional message forwarding.

        Implementations should read from *ws* (the client), run inference,
        and write responses back.  The method returns when the connection
        closes.
        """
        ...

    @abstractmethod
    async def start_background_tasks(self) -> None:
        """Start any periodic background tasks (token refresh, model warm-up, etc.)."""
        ...

    @abstractmethod
    async def stop_background_tasks(self) -> None:
        """Stop background tasks on shutdown."""
        ...
