"""Configuration and ownership of a reusable Azure Monitor SDK client."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID


@dataclass(frozen=True)
class ConnectionInfo:
    """Trusted workspace and cloud settings, independent of query arguments."""

    workspace_id: str
    endpoint: str = "https://api.loganalytics.azure.com"
    audience: str = "https://api.loganalytics.io"

    def __post_init__(self) -> None:
        try:
            workspace = str(UUID(self.workspace_id))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError("workspace_id must be a UUID") from exc
        object.__setattr__(self, "workspace_id", workspace)
        for name in ("endpoint", "audience"):
            value = getattr(self, name)
            parsed = urlsplit(value)
            if (
                parsed.scheme != "https"
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.path not in ("", "/")
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError(f"{name} must be an HTTPS origin")


def _make_client(credential: Any, info: ConnectionInfo) -> Any:
    try:
        from azure.monitor.query import LogsQueryClient
    except ImportError as exc:
        raise RuntimeError("Install agent-data-toolkit[azure-monitor]") from exc
    return LogsQueryClient(credential, endpoint=info.endpoint, audience=info.audience)


def _environment_credential(auth: str, client_id: str | None) -> Any:
    try:
        from azure.identity import AzureCliCredential, ManagedIdentityCredential
    except ImportError as exc:
        raise RuntimeError("Install agent-data-toolkit[azure-monitor]") from exc
    if auth == "managed-identity":
        return (
            ManagedIdentityCredential(client_id=client_id)
            if client_id
            else ManagedIdentityCredential()
        )
    if auth == "azure-cli":
        if client_id:
            raise ValueError("client_id is only valid for managed-identity authentication")
        return AzureCliCredential()
    raise ValueError("auth must be managed-identity or azure-cli")


class ConnectionManager:
    """Reuse one SDK client/credential. Injected credentials remain caller-owned.

    The SDK handles authentication and token renewal. The connector never accepts
    a raw token string, silently falls back between identities, or persists tokens.
    """

    def __init__(self, info: ConnectionInfo, credential: Any, *, owns_credential: bool = False):
        if not callable(getattr(credential, "get_token", None)):
            raise TypeError("credential must implement the synchronous TokenCredential protocol")
        self.info = info
        self._credential = credential
        self._owns_credential = owns_credential
        self._client: Any = None
        self._closed = False

    @classmethod
    def from_env(cls, *, credential: Any = None) -> ConnectionManager:
        """Read AZURE_LOGS_WORKSPACE_ID and explicit AZURE_LOGS_AUTH settings.

        AZURE_LOGS_AUTH defaults to managed-identity; azure-cli is an explicit
        local-development option. AZURE_CLIENT_ID selects a user-assigned identity.
        Cloud overrides are AZURE_LOGS_ENDPOINT and AZURE_LOGS_AUDIENCE.
        """
        info = ConnectionInfo(
            workspace_id=os.environ.get("AZURE_LOGS_WORKSPACE_ID", ""),
            endpoint=os.environ.get("AZURE_LOGS_ENDPOINT", ConnectionInfo.endpoint),
            audience=os.environ.get("AZURE_LOGS_AUDIENCE", ConnectionInfo.audience),
        )
        if credential is not None:
            return cls(info, credential)
        auth = os.environ.get("AZURE_LOGS_AUTH", "managed-identity")
        identity = _environment_credential(auth, os.environ.get("AZURE_CLIENT_ID"))
        return cls(info, identity, owns_credential=True)

    @property
    def client(self) -> Any:
        """Lazily construct the reusable service client; failed construction is retryable."""
        if self._closed:
            raise RuntimeError("Azure Monitor Logs client is closed")
        if self._client is None:
            self._client = _make_client(self._credential, self.info)
        return self._client

    def close(self) -> None:
        """Close owned resources once, preserving caller-owned credentials."""
        if self._closed:
            return
        self._closed = True
        try:
            if self._client is not None:
                self._client.close()
        finally:
            if self._owns_credential:
                self._credential.close()
