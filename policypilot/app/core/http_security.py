"""Bound private request bodies before JSON parsing and prevent response caching."""

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

_PRIVATE_PATHS = frozenset(
    {
        "/api/v1/auth/login",
        "/api/v1/auth/logout",
        "/api/v1/me",
        "/api/v1/query",
        "/api/v1/policies",
        "/api/v1/admin/overview",
    }
)


def _private(path: str) -> bool:
    return path in _PRIVATE_PATHS or path.startswith("/api/v1/sources/")


class AuthenticationBoundary:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not _private(scope["path"]):
            await self.app(scope, receive, send)
            return

        async def private_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.extend([(b"cache-control", b"no-store"), (b"pragma", b"no-cache")])
                message = {**message, "headers": headers}
            await send(message)

        # Buffer no more than 4 KiB, including chunked requests without Content-Length.
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > 4096:
                response = JSONResponse({"detail": "Request too large"}, status_code=413)
                await response(scope, receive, private_send)
                return
            body.extend(chunk)
            if not message.get("more_body", False):
                break

        async def bounded_receive() -> Message:
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        await self.app(scope, bounded_receive, private_send)
