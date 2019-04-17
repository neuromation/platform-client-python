import os
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, Optional

import aiohttp
import yaml
from yarl import URL

from .client import Client
from .config import _Config, _PyPIVersion
from .core import DEFAULT_TIMEOUT
from .login import AuthNegotiator, _AuthConfig, _AuthToken, get_server_config


WIN32 = sys.platform == "win32"
MALFORMED_CONFIG_TEXT = "Malformed config. Please logout and login again."
DEFAULT_CONFIG_PATH = "~/.nmrc"
CONFIG_ENV_NAME = "NEUROMATION_CONFIG"
DEFAULT_API_URL = URL("https://staging.neu.ro/api/v1")


class ConfigError(RuntimeError):
    pass


class Factory:
    def __init__(self, path: Optional[Path] = None) -> None:
        if path is None:
            path = Path(os.environ.get(CONFIG_ENV_NAME, DEFAULT_CONFIG_PATH))
        self._path = path.expanduser()

    async def get(self, *, timeout: aiohttp.ClientTimeout = DEFAULT_TIMEOUT) -> Client:
        config = self._read()
        new_token = await self._refresh_auth_token(config)
        if new_token != config.auth_token:
            new_config = replace(config, auth_token=new_token)
            self._save(new_config)
            return Client(new_config, timeout=timeout)
        return Client(config, timeout=timeout)

    async def login(
        self,
        *,
        url: URL = DEFAULT_API_URL,
        timeout: aiohttp.ClientTimeout = DEFAULT_TIMEOUT,
    ) -> None:
        if self._path.exists():
            raise ConfigError(f"Config file {self._path} already exists. Please logout")
        server_config = await get_server_config(url)
        negotiator = AuthNegotiator(server_config.auth_config)
        auth_token = await negotiator.refresh_token()
        config = _Config(
            auth_config=server_config.auth_config,
            auth_token=auth_token,
            pypi=_PyPIVersion.create_uninitialized(),
            url=url,
            registry_url=server_config.registry_url,
        )
        async with Client(config, timeout=timeout) as client:
            await client.jobs.list()  # raises an exception if cannot login
        self._save(config)

    async def login_with_token(
        self,
        token: str,
        *,
        url: URL = DEFAULT_API_URL,
        timeout: aiohttp.ClientTimeout = DEFAULT_TIMEOUT,
    ) -> None:
        if self._path.exists():
            raise ConfigError(f"Config file {self._path} already exists. Please logout")
        server_config = await get_server_config(url)
        config = _Config(
            auth_config=server_config.auth_config,
            auth_token=_AuthToken.create_non_expiring(token),
            pypi=_PyPIVersion.create_uninitialized(),
            url=url,
            registry_url=server_config.registry_url,
        )
        async with Client(config, timeout=timeout) as client:
            await client.jobs.list()  # raises an exception if cannot login
        self._save(config)

    async def logout(self) -> None:
        # TODO: logout from auth0
        if self._path.exists():
            self._path.unlink()

    def _read(self) -> _Config:
        if not self._path.exists():
            raise ConfigError(f"Config file {self._path} does not exists. Please login")
        if not self._path.is_file():
            raise ConfigError(f"Config {self._path} is not a regular file")

        stat = self._path.stat()
        if not WIN32 and stat.st_mode & 0o777 != 0o600:
            raise ConfigError(
                f"Config file {self._path} has compromised permission bits, "
                f"run 'chmod 600 {self._path}' first"
            )
        with self._path.open("r") as f:
            payload = yaml.safe_load(f)

        try:
            api_url = URL(payload["url"])
            registry_url = URL(payload["registry_url"])
            pypi_payload = payload["pypi"]
        except (KeyError, TypeError, ValueError):
            raise ConfigError(MALFORMED_CONFIG_TEXT)

        auth_config = self._deserialize_auth_config(payload)
        auth_token = self._deserialize_auth_token(payload)

        return _Config(
            auth_config=auth_config,
            auth_token=auth_token,
            pypi=_PyPIVersion.from_config(pypi_payload),
            url=api_url,
            registry_url=registry_url,
        )

    def _serialize_auth_config(self, auth_config: _AuthConfig) -> Dict[str, Any]:
        assert auth_config.is_initialized(), auth_config
        success_redirect_url = None
        if auth_config.success_redirect_url:
            success_redirect_url = str(auth_config.success_redirect_url)
        return {
            "auth_url": str(auth_config.auth_url),
            "token_url": str(auth_config.token_url),
            "client_id": auth_config.client_id,
            "audience": auth_config.audience,
            "success_redirect_url": success_redirect_url,
            "callback_urls": [str(u) for u in auth_config.callback_urls],
        }

    def _deserialize_auth_config(self, payload: Dict[str, Any]) -> _AuthConfig:
        auth_config = payload.get("auth_config")
        if not auth_config:
            raise ConfigError(MALFORMED_CONFIG_TEXT)
        success_redirect_url = auth_config.get("success_redirect_url")
        if success_redirect_url:
            success_redirect_url = URL(success_redirect_url)
        return _AuthConfig(
            auth_url=URL(auth_config["auth_url"]),
            token_url=URL(auth_config["token_url"]),
            client_id=auth_config["client_id"],
            audience=auth_config["audience"],
            success_redirect_url=success_redirect_url,
            callback_urls=tuple(URL(u) for u in auth_config.get("callback_urls", [])),
        )

    def _deserialize_auth_token(self, payload: Dict[str, Any]) -> _AuthToken:
        auth_payload = payload.get("auth_token")
        if auth_payload is None:
            raise ConfigError(MALFORMED_CONFIG_TEXT)
        return _AuthToken(
            token=auth_payload["token"],
            expiration_time=auth_payload["expiration_time"],
            refresh_token=auth_payload["refresh_token"],
        )

    async def _refresh_auth_token(self, config: _Config) -> _AuthToken:
        auth_negotiator = AuthNegotiator(config=config.auth_config)
        return await auth_negotiator.refresh_token(config.auth_token)

    def _save(self, config: _Config) -> None:
        payload: Dict[str, Any] = {
            "url": str(config.url),
            "registry_url": str(config.registry_url),
        }
        payload["auth_config"] = self._serialize_auth_config(config.auth_config)
        payload["auth_token"] = {
            "token": config.auth_token.token,
            "expiration_time": config.auth_token.expiration_time,
            "refresh_token": config.auth_token.refresh_token,
        }
        payload["pypi"] = config.pypi.to_config()

        # forbid access to other users
        if self._path.exists():
            # drop a file if exists to reopen it in exclusive mode for writing
            self._path.unlink()
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        with os.fdopen(os.open(self._path, flags, 0o600), "w") as f:
            yaml.safe_dump(payload, f, default_flow_style=False)

    def _update_last_checked_version(self, version: Any, timestamp: int) -> None:
        config = self._read()
        new_config = replace(
            config, pypi=_PyPIVersion(pypi_version=version, check_timestamp=timestamp)
        )
        self._save(new_config)