"""One way for maintenance scripts to reach a workspace, from a laptop or a cloud session.

A laptop uses a CLI profile (``databricks auth login --profile idp-mvp``). A cloud session has no
profile file, so it uses ``DATABRICKS_HOST`` and ``DATABRICKS_TOKEN`` from its environment
settings instead; the token then stays out of the repository and the chat. Either way the client
must point at the workspace the caller named explicitly.
"""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlsplit


def plain_origin(host: str) -> str:
    origin = urlsplit(host)
    if (
        origin.scheme != "https"
        or not origin.hostname
        or origin.username
        or origin.password
        or origin.path not in ("", "/")
        or origin.query
        or origin.fragment
    ):
        raise ValueError("The host must be the plain HTTPS workspace origin")
    return host.rstrip("/")


def workspace_client(profile: str, host: str, **timeouts: Any) -> Any:
    """Token from the environment when both variables are set, otherwise the CLI profile."""
    from databricks.sdk import WorkspaceClient
    from databricks.sdk.core import Config

    expected = plain_origin(host)
    settings = {"http_timeout_seconds": 15, "retry_timeout_seconds": 1, **timeouts}
    if os.environ.get("DATABRICKS_TOKEN") and os.environ.get("DATABRICKS_HOST"):
        if plain_origin(os.environ["DATABRICKS_HOST"]) != expected:
            raise ValueError("DATABRICKS_HOST does not match the explicitly selected workspace")
        config = Config(host=expected, token=os.environ["DATABRICKS_TOKEN"], **settings)
    else:
        config = Config(profile=profile, **settings)
    client = WorkspaceClient(config=config)
    if client.config.host.rstrip("/") != expected:
        raise ValueError("Profile host does not match the explicitly selected workspace")
    return client
