from __future__ import annotations

from app.external.backends.base import ExternalExecutionBackend


class ExternalBackendRegistry:
    def __init__(self) -> None:
        self._backends: dict[str, ExternalExecutionBackend] = {}

    def register(self, backend: ExternalExecutionBackend) -> None:
        self._backends[backend.name] = backend

    def get(self, name: str) -> ExternalExecutionBackend | None:
        return self._backends.get(name)

    def names(self) -> list[str]:
        return sorted(self._backends)

    def clear(self) -> None:
        self._backends.clear()

    async def aclose(self) -> None:
        for backend in self._backends.values():
            close = getattr(backend, "aclose", None)
            if close:
                await close()


external_backends = ExternalBackendRegistry()
