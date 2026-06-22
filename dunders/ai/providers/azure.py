"""``AzureOpenAIProvider`` — Azure's OpenAI service over stdlib HTTP.

Azure's fields differ from vanilla OpenAI (``azure_endpoint`` + ``api_version``
+ a ``deployment`` that stands in for the model) and it authenticates with an
``api-key`` header against deployment-scoped URLs, so it overrides the URL/auth
builders. The request/response mapping is inherited from
``OpenAICompatProvider``. No ``openai`` SDK — plain REST.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from dunders.ai.providers.openai_compat import OpenAICompatProvider
from dunders.ai.types import FieldSpec


if TYPE_CHECKING:
    from dunders.ai.secrets import SecretResolver


__all__ = ["AzureOpenAIProvider"]


class AzureOpenAIProvider(OpenAICompatProvider):
    name = "azure"

    def __init__(
        self,
        *,
        azure_endpoint: str | None,
        api_version: str,
        deployment: str,
        api_key: str | None,
    ) -> None:
        super().__init__(base_url=None, api_key=api_key, model=deployment, name="azure")
        self.azure_endpoint = (azure_endpoint or "").rstrip("/")
        self.api_version = api_version

    @classmethod
    def config_schema(cls) -> list[FieldSpec]:
        return [
            FieldSpec("azure_endpoint", "Azure endpoint", kind="url",
                      help="https://<resource>.openai.azure.com"),
            FieldSpec("api_version", "API version", default="2024-10-21"),
            FieldSpec("deployment", "Deployment name",
                      help="The Azure deployment (used as the model)"),
            FieldSpec("api_key", "API key", kind="secret",
                      default="AZURE_OPENAI_API_KEY",
                      help="Env var name or the key itself"),
        ]

    @classmethod
    def from_config(
        cls, cfg: Mapping[str, Any], secrets: "SecretResolver"
    ) -> "AzureOpenAIProvider":
        key_ref = str(cfg.get("api_key", "AZURE_OPENAI_API_KEY"))
        return cls(
            azure_endpoint=cfg.get("azure_endpoint"),
            api_version=str(cfg.get("api_version", "2024-10-21")),
            deployment=str(cfg.get("deployment", "")),
            api_key=secrets.resolve(key_ref) or key_ref,
        )

    # --- Azure endpoint shape ---------------------------------------------

    def _headers(self) -> dict[str, str]:
        return {"api-key": self._api_key or ""}

    def _chat_url(self) -> str:
        return (
            f"{self.azure_endpoint}/openai/deployments/{self.model}"
            f"/chat/completions?api-version={self.api_version}"
        )

    def _models_url(self) -> str:
        return f"{self.azure_endpoint}/openai/models?api-version={self.api_version}"
