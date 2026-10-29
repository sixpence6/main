from __future__ import annotations

from collections.abc import Callable

import httpx

from app.adapters.base import ImageAdapter, ImageCapabilities, LLMAdapter
from app.adapters.openai_images import OPENAI_IMAGES_CAPABILITIES, OpenAIImagesAdapter
from app.config import RoleConfig

LLMFactory = Callable[[RoleConfig, httpx.AsyncClient], LLMAdapter]
VisualFactory = Callable[[RoleConfig, httpx.AsyncClient], ImageAdapter]


class AdapterRegistry:
    """Explicit registration table. No production fallback to a test double exists."""

    def __init__(self) -> None:
        self._llm: dict[str, LLMFactory] = {}
        self._visual: dict[str, VisualFactory] = {}
        self._visual_capabilities: dict[str, ImageCapabilities | None] = {}

    def register_llm(self, adapter_id: str, factory: LLMFactory) -> None:
        self._llm[adapter_id] = factory

    def register_visual(
        self,
        adapter_id: str,
        factory: VisualFactory,
        capabilities: ImageCapabilities | None = None,
    ) -> None:
        self._visual[adapter_id] = factory
        self._visual_capabilities[adapter_id] = capabilities

    def known_llm_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._llm))

    def known_visual_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._visual))

    def visual_capabilities(self, adapter_id: str) -> ImageCapabilities | None:
        return self._visual_capabilities.get(adapter_id)

    def build_llm(self, role: RoleConfig, client: httpx.AsyncClient) -> LLMAdapter | None:
        factory = self._llm.get(role.adapter_id or "")
        return factory(role, client) if factory else None

    def build_visual(self, role: RoleConfig, client: httpx.AsyncClient) -> ImageAdapter | None:
        factory = self._visual.get(role.adapter_id or "")
        return factory(role, client) if factory else None


def build_default_registry() -> AdapterRegistry:
    """Production registry with the built-in OpenAI-compatible image adapter.

    The adapter is configuration-driven: base URL, key, model, and image size live
    in the environment. Unknown adapter ids still resolve to nothing and are
    reported as not ready rather than falling back to a fake implementation.
    """
    registry = AdapterRegistry()
    registry.register_visual(
        OpenAIImagesAdapter.adapter_id,
        OpenAIImagesAdapter,
        capabilities=OPENAI_IMAGES_CAPABILITIES,
    )
    return registry
