"""DKN Cloud NA API client."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

import aiohttp
import socketio

from .const import (
    API_BASE_PATH,
    API_BASE_URL,
    API_INSTALLATIONS_PATH,
    API_LOGGED_IN_PATH,
    API_LOGIN_PATH,
    API_REFRESH_TOKEN_PATH,
    API_SOCKET_PATH,
    EVENT_CONTROL_DELETED_DEVICE,
    EVENT_CONTROL_DELETED_INSTALLATION,
    EVENT_CONTROL_NEW_DEVICE,
    EVENT_CREATE_MACHINE_EVENT,
    EVENT_DEVICE_DATA,
    USER_AGENT,
)

_LOGGER = logging.getLogger(__name__)


class DknCloudApiError(Exception):
    """Base exception for DKN Cloud API errors."""


class DknAuthError(DknCloudApiError):
    """Authentication error."""


class DknConnectionError(DknCloudApiError):
    """Connection error."""


class DknCloudApi:
    """Client for the DKN Cloud NA API."""

    def __init__(self, email: str, password: str) -> None:
        """Initialize the API client."""
        self.email = email
        self.password = password
        self.token: str | None = None
        self.refresh_token: str | None = None
        self._base_url = API_BASE_URL
        self._api_url = f"{API_BASE_URL}{API_BASE_PATH}"
        self._session: aiohttp.ClientSession | None = None
        self._sio_clients: dict[str, socketio.AsyncClient] = {}
        self._installations: list[dict[str, Any]] = []
        self._devices: dict[str, dict[str, Any]] = {}
        self._device_callbacks: list[Callable] = []
        self._connected = False

    @property
    def devices(self) -> dict[str, dict[str, Any]]:
        """Return discovered devices keyed by MAC address."""
        return self._devices

    def _headers(self, with_auth: bool = True) -> dict[str, str]:
        """Build request headers."""
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        }
        if with_auth and self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    async def _ensure_session(self) -> aiohttp.ClientSession:
        """Get or create the aiohttp session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def authenticate(self) -> bool:
        """Authenticate with the DKN Cloud NA API.

        Tries: existing token -> refresh token -> email/password login.
        Returns True on success.
        """
        if self.token:
            if await self._check_logged_in():
                return True
            if self.refresh_token:
                if await self._refresh_token():
                    return True

        return await self._login()

    async def _check_logged_in(self) -> bool:
        """Check if current token is still valid."""
        session = await self._ensure_session()
        try:
            async with session.get(
                f"{self._api_url}{API_LOGGED_IN_PATH}",
                headers=self._headers(),
            ) as resp:
                return resp.status == 200
        except aiohttp.ClientError:
            return False

    async def _refresh_token(self) -> bool:
        """Refresh the authentication token."""
        session = await self._ensure_session()
        try:
            async with session.get(
                f"{self._api_url}{API_REFRESH_TOKEN_PATH}{self.refresh_token}",
                headers=self._headers(with_auth=False),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    self.token = data.get("token")
                    self.refresh_token = data.get("refreshToken", self.refresh_token)
                    _LOGGER.debug("Token refreshed successfully")
                    return True
                return False
        except aiohttp.ClientError:
            return False

    async def _login(self) -> bool:
        """Login with email and password."""
        session = await self._ensure_session()
        try:
            async with session.post(
                f"{self._api_url}{API_LOGIN_PATH}",
                headers=self._headers(with_auth=False),
                json={"email": self.email, "password": self.password},
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    self.token = data.get("token")
                    self.refresh_token = data.get("refreshToken")
                    _LOGGER.debug("Login successful")
                    return True
                _LOGGER.error("Login failed with status %s", resp.status)
                raise DknAuthError(f"Login failed with status {resp.status}")
        except aiohttp.ClientError as err:
            raise DknConnectionError(f"Connection error during login: {err}") from err

    async def get_installations(self) -> list[dict[str, Any]]:
        """Fetch all installations and their devices."""
        session = await self._ensure_session()
        try:
            async with session.get(
                f"{self._api_url}{API_INSTALLATIONS_PATH}",
                headers=self._headers(),
            ) as resp:
                if resp.status == 200:
                    self._installations = await resp.json()
                    _LOGGER.debug(
                        "Raw installations response: %s", self._installations
                    )
                    self._process_installations()
                    return self._installations
                if resp.status == 401:
                    if await self.authenticate():
                        return await self.get_installations()
                    raise DknAuthError("Re-authentication failed")
                raise DknCloudApiError(
                    f"Failed to get installations: {resp.status}"
                )
        except aiohttp.ClientError as err:
            raise DknConnectionError(f"Connection error: {err}") from err

    def _process_installations(self) -> None:
        """Extract devices from installations."""
        for install in self._installations:
            for device in install.get("devices", []):
                mac = device.get("mac")
                if mac:
                    self._devices[mac] = {
                        "installation_id": install["_id"],
                        "mac": mac,
                        "name": device.get("name", f"DKN {mac}"),
                        "icon": device.get("icon", ""),
                        "data": device,
                    }
                    _LOGGER.debug(
                        "Device %s (%s) data keys: %s",
                        device.get("name"),
                        mac,
                        list(device.keys()),
                    )
                    _LOGGER.debug(
                        "Device %s full data: %s", mac, device
                    )

    async def connect_socket(self) -> None:
        """Establish socket.io connections for real-time updates."""
        if not self.token:
            raise DknAuthError("Must authenticate before connecting socket")

        await self._connect_users_namespace()

        for install in self._installations:
            install_id = install["_id"]
            await self._connect_installation_namespace(install_id)

        self._connected = True
        _LOGGER.debug("Socket.io connections established")

    def _create_sio_client(self) -> socketio.AsyncClient:
        """Create a socket.io client with appropriate settings."""
        return socketio.AsyncClient(
            reconnection=True,
            reconnection_attempts=5,
            reconnection_delay=1,
            reconnection_delay_max=5,
            logger=_LOGGER.isEnabledFor(logging.DEBUG),
            engineio_logger=_LOGGER.isEnabledFor(logging.DEBUG),
        )

    async def _sio_connect(
        self, sio: socketio.AsyncClient, namespaces: list[str]
    ) -> None:
        """Connect a socket.io client, trying websocket first then polling."""
        connect_kwargs = {
            "socketio_path": API_SOCKET_PATH,
            "namespaces": namespaces,
            "headers": {
                "Authorization": f"Bearer {self.token}",
                "User-Agent": USER_AGENT,
            },
            "auth": {"token": self.token},
        }

        # Try websocket first, then fall back to polling
        for transports in [["websocket"], ["polling", "websocket"]]:
            try:
                await sio.connect(
                    self._base_url,
                    transports=transports,
                    **connect_kwargs,
                )
                _LOGGER.debug(
                    "Socket.io connected with transports=%s", transports
                )
                return
            except Exception as err:
                _LOGGER.debug(
                    "Socket.io connect with transports=%s failed: %s",
                    transports,
                    err,
                )

        raise DknConnectionError("All socket.io transport methods failed")

    async def _connect_users_namespace(self) -> None:
        """Connect to the /users namespace."""
        sio = self._create_sio_client()

        @sio.on(EVENT_CONTROL_NEW_DEVICE, namespace="/users")
        async def on_new_device(data: dict) -> None:
            _LOGGER.info("New device detected: %s", data)
            await self.get_installations()
            self._notify_device_update()

        @sio.on(EVENT_CONTROL_DELETED_DEVICE, namespace="/users")
        async def on_deleted_device(data: dict) -> None:
            _LOGGER.info("Device removed: %s", data)
            mac = data.get("mac")
            if mac and mac in self._devices:
                del self._devices[mac]
            self._notify_device_update()

        @sio.on(EVENT_CONTROL_DELETED_INSTALLATION, namespace="/users")
        async def on_deleted_installation(data: dict) -> None:
            _LOGGER.info("Installation removed: %s", data)

        @sio.on("connect", namespace="/users")
        async def on_connect() -> None:
            _LOGGER.debug("Connected to /users namespace")

        @sio.on("connect_error", namespace="/users")
        async def on_connect_error(data: Any) -> None:
            _LOGGER.error("Socket.io /users connect error: %s", data)

        @sio.on("disconnect", namespace="/users")
        async def on_disconnect() -> None:
            _LOGGER.debug("Disconnected from /users namespace")

        # Catch-all for debugging unknown events
        @sio.on("*", namespace="/users")
        async def on_any(event: str, data: Any) -> None:
            _LOGGER.debug("Socket.io /users event '%s': %s", event, data)

        try:
            await self._sio_connect(sio, ["/users"])
            self._sio_clients["users"] = sio
        except DknConnectionError:
            raise
        except Exception as err:
            _LOGGER.error("Failed to connect to /users namespace: %s", err)
            raise DknConnectionError(
                f"Socket.io connection failed: {err}"
            ) from err

    async def _connect_installation_namespace(
        self, installation_id: str
    ) -> None:
        """Connect to an installation namespace for device data."""
        namespace = f"/{installation_id}::dknUsa"
        sio = self._create_sio_client()

        @sio.on(EVENT_DEVICE_DATA, namespace=namespace)
        async def on_device_data(data: dict) -> None:
            _LOGGER.debug(
                "Raw device-data event on %s: %s", namespace, data
            )
            mac = data.get("mac")
            if mac and mac in self._devices:
                device = self._devices[mac]
                device["data"].update(data)
                self._notify_device_update(mac)

        @sio.on("connect", namespace=namespace)
        async def on_connect() -> None:
            _LOGGER.debug("Connected to namespace %s", namespace)

        @sio.on("connect_error", namespace=namespace)
        async def on_connect_error(data: Any) -> None:
            _LOGGER.error(
                "Socket.io %s connect error: %s", namespace, data
            )

        @sio.on("disconnect", namespace=namespace)
        async def on_disconnect() -> None:
            _LOGGER.debug("Disconnected from namespace %s", namespace)

        # Catch-all for debugging unknown events
        @sio.on("*", namespace=namespace)
        async def on_any(event: str, data: Any) -> None:
            _LOGGER.debug(
                "Socket.io %s event '%s': %s", namespace, event, data
            )

        try:
            await self._sio_connect(sio, [namespace])
            self._sio_clients[installation_id] = sio
        except DknConnectionError:
            raise
        except Exception as err:
            _LOGGER.error(
                "Failed to connect to namespace %s: %s", namespace, err
            )
            raise DknConnectionError(
                f"Socket.io connection to {namespace} failed: {err}"
            ) from err

    async def send_command(
        self, mac: str, prop: str, value: Any
    ) -> None:
        """Send a command to a device."""
        device = self._devices.get(mac)
        if not device:
            raise DknCloudApiError(f"Unknown device: {mac}")

        installation_id = device["installation_id"]
        sio = self._sio_clients.get(installation_id)
        if not sio or not sio.connected:
            _LOGGER.warning(
                "Socket not connected for installation %s, reconnecting",
                installation_id,
            )
            await self._connect_installation_namespace(installation_id)
            sio = self._sio_clients.get(installation_id)

        namespace = f"/{installation_id}::dknUsa"
        await sio.emit(
            EVENT_CREATE_MACHINE_EVENT,
            {"mac": mac, "property": prop, "value": value},
            namespace=namespace,
        )
        _LOGGER.debug("Sent command to %s: %s=%s", mac, prop, value)

        # Optimistically update local state
        device["data"][prop] = value
        self._notify_device_update(mac)

    def register_device_callback(self, callback: Callable) -> Callable:
        """Register a callback for device updates. Returns unregister function."""
        self._device_callbacks.append(callback)

        def unregister() -> None:
            self._device_callbacks.remove(callback)

        return unregister

    def _notify_device_update(self, mac: str | None = None) -> None:
        """Notify all registered callbacks of device updates."""
        for callback in self._device_callbacks:
            try:
                callback(mac)
            except Exception:
                _LOGGER.exception("Error in device update callback")

    async def disconnect(self) -> None:
        """Disconnect all socket.io clients and close session."""
        for key, sio in self._sio_clients.items():
            try:
                await sio.disconnect()
            except Exception:
                _LOGGER.debug("Error disconnecting socket %s", key)
        self._sio_clients.clear()
        self._connected = False

        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def test_connection(self) -> bool:
        """Test the connection by authenticating and fetching installations.

        Used during config flow validation.
        """
        try:
            await self.authenticate()
            await self.get_installations()
            return True
        except DknAuthError:
            raise
        except DknConnectionError:
            raise
        finally:
            if self._session and not self._session.closed:
                await self._session.close()
                self._session = None
