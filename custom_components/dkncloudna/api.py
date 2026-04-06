"""DKN Cloud NA API client.

Implements Socket.IO v2 (Engine.IO v3) protocol directly using aiohttp
websockets, since the DKN Cloud NA server uses Socket.IO v2 and the
python-socketio v5 library only speaks Socket.IO v5 (EIO=4).
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable

import aiohttp

from .const import (
    API_BASE_PATH,
    API_BASE_URL,
    API_INSTALLATIONS_PATH,
    API_LOGGED_IN_PATH,
    API_LOGIN_PATH,
    API_REFRESH_TOKEN_PATH,
    API_SOCKET_PATH,
    EVENT_CREATE_MACHINE_EVENT,
    EVENT_DEVICE_DATA,
    USER_AGENT,
)

_LOGGER = logging.getLogger(__name__)

# Engine.IO v3 packet types
EIO_OPEN = "0"
EIO_CLOSE = "1"
EIO_PING = "2"
EIO_PONG = "3"
EIO_MESSAGE = "4"

# Socket.IO v2 packet types (carried inside EIO_MESSAGE)
SIO_CONNECT = "0"
SIO_DISCONNECT = "1"
SIO_EVENT = "2"
SIO_ACK = "3"
SIO_ERROR = "4"


class DknCloudApiError(Exception):
    """Base exception for DKN Cloud API errors."""


class DknAuthError(DknCloudApiError):
    """Authentication error."""


class DknConnectionError(DknCloudApiError):
    """Connection error."""


class SocketIOv2Connection:
    """A single Socket.IO v2 connection managing multiple namespaces."""

    def __init__(
        self,
        base_url: str,
        socket_path: str,
        token: str,
        session: aiohttp.ClientSession,
    ) -> None:
        """Initialize the Socket.IO v2 connection."""
        self._base_url = base_url
        self._socket_path = socket_path
        self._token = token
        self._session = session
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._event_handlers: dict[str, dict[str, list[Callable]]] = {}
        self._namespaces: list[str] = []
        self._connected_namespaces: set[str] = set()
        self._ping_interval: float = 25.0
        self._ping_timeout: float = 60.0
        self._listener_task: asyncio.Task | None = None
        self._ping_task: asyncio.Task | None = None
        self._connected = False
        self._ack_id = 0

    @property
    def connected(self) -> bool:
        """Return whether the connection is active."""
        return self._connected and self._ws is not None and not self._ws.closed

    def on(self, event: str, namespace: str, handler: Callable) -> None:
        """Register an event handler for a namespace."""
        if namespace not in self._event_handlers:
            self._event_handlers[namespace] = {}
        if event not in self._event_handlers[namespace]:
            self._event_handlers[namespace][event] = []
        self._event_handlers[namespace][event].append(handler)

    async def connect(self, namespaces: list[str]) -> None:
        """Establish the websocket connection and join namespaces."""
        self._namespaces = namespaces

        # Build the websocket URL with EIO=3 for Socket.IO v2
        ws_url = (
            f"{self._base_url.replace('https://', 'wss://').replace('http://', 'ws://')}"
            f"/{self._socket_path.strip('/')}/"
            f"?transport=websocket&EIO=3"
        )

        _LOGGER.debug("Connecting to Socket.IO v2: %s", ws_url)

        try:
            self._ws = await self._session.ws_connect(
                ws_url,
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "User-Agent": USER_AGENT,
                },
            )
        except Exception as err:
            _LOGGER.error("WebSocket connection failed: %s", err)
            raise DknConnectionError(
                f"WebSocket connection failed: {err}"
            ) from err

        # Read the EIO open packet to get ping interval
        msg = await self._ws.receive(timeout=10)
        if msg.type == aiohttp.WSMsgType.TEXT and msg.data.startswith(EIO_OPEN):
            open_data = json.loads(msg.data[1:])
            self._ping_interval = open_data.get("pingInterval", 25000) / 1000
            self._ping_timeout = open_data.get("pingTimeout", 60000) / 1000
            _LOGGER.debug(
                "EIO open: pingInterval=%.1fs, pingTimeout=%.1fs, sid=%s",
                self._ping_interval,
                self._ping_timeout,
                open_data.get("sid"),
            )
        else:
            _LOGGER.error("Unexpected first message: %s", msg)
            await self._ws.close()
            raise DknConnectionError("Did not receive EIO open packet")

        # The default namespace "/" is auto-connected.
        # Read the SIO connect for "/" namespace
        msg = await self._ws.receive(timeout=10)
        if msg.type == aiohttp.WSMsgType.TEXT:
            _LOGGER.debug("Default namespace connect message: %s", msg.data)
            self._connected_namespaces.add("/")

        self._connected = True

        # Connect to additional namespaces
        for ns in namespaces:
            if ns != "/":
                # Socket.IO v2: send "40/namespace," to connect
                connect_msg = f"{EIO_MESSAGE}{SIO_CONNECT}{ns},"
                _LOGGER.debug("Joining namespace: %s", ns)
                await self._ws.send_str(connect_msg)

        # Start background tasks
        self._listener_task = asyncio.create_task(self._listen())
        self._ping_task = asyncio.create_task(self._ping_loop())

    async def _listen(self) -> None:
        """Listen for incoming messages."""
        try:
            async for msg in self._ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    await self._handle_message(msg.data)
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    _LOGGER.error("WebSocket error: %s", self._ws.exception())
                    break
                elif msg.type in (
                    aiohttp.WSMsgType.CLOSED,
                    aiohttp.WSMsgType.CLOSING,
                ):
                    break
        except asyncio.CancelledError:
            return
        except Exception:
            _LOGGER.exception("Error in socket listener")
        finally:
            self._connected = False
            _LOGGER.debug("Socket listener ended")

    async def _handle_message(self, raw: str) -> None:
        """Parse and handle an incoming Socket.IO v2 message."""
        if not raw:
            return

        # Engine.IO level
        eio_type = raw[0]

        if eio_type == EIO_PING:
            # Server sent ping, respond with pong
            await self._ws.send_str(EIO_PONG)
            return

        if eio_type == EIO_PONG:
            return

        if eio_type == EIO_CLOSE:
            _LOGGER.debug("Received EIO close")
            self._connected = False
            return

        if eio_type != EIO_MESSAGE:
            _LOGGER.debug("Unhandled EIO packet type: %s", eio_type)
            return

        # Socket.IO level - strip EIO_MESSAGE prefix
        sio_data = raw[1:]
        if not sio_data:
            return

        sio_type = sio_data[0]
        sio_payload = sio_data[1:]

        if sio_type == SIO_CONNECT:
            # Namespace connected
            ns = sio_payload.rstrip(",") if sio_payload else "/"
            self._connected_namespaces.add(ns)
            _LOGGER.debug("Connected to namespace: %s", ns)
            await self._emit_handlers("connect", ns, None)
            return

        if sio_type == SIO_DISCONNECT:
            ns = sio_payload or "/"
            self._connected_namespaces.discard(ns)
            _LOGGER.debug("Disconnected from namespace: %s", ns)
            await self._emit_handlers("disconnect", ns, None)
            return

        if sio_type == SIO_ERROR:
            _LOGGER.error("Socket.IO error: %s", sio_payload)
            return

        if sio_type == SIO_EVENT:
            await self._handle_event(sio_payload)
            return

        _LOGGER.debug("Unhandled SIO packet type: %s, data: %s", sio_type, sio_payload)

    async def _handle_event(self, payload: str) -> None:
        """Handle a Socket.IO event packet."""
        # Parse namespace from payload: "/namespace,{ack_id}[data]"
        namespace = "/"
        data_str = payload

        if payload.startswith("/"):
            # Has namespace prefix
            comma_idx = payload.index(",")
            namespace = payload[:comma_idx]
            data_str = payload[comma_idx + 1:]

        # Strip optional ack ID (digits before the JSON array)
        idx = 0
        while idx < len(data_str) and data_str[idx].isdigit():
            idx += 1
        data_str = data_str[idx:]

        try:
            event_data = json.loads(data_str)
        except json.JSONDecodeError:
            _LOGGER.debug(
                "Failed to parse event data: %s (ns=%s)", data_str, namespace
            )
            return

        if isinstance(event_data, list) and len(event_data) >= 1:
            event_name = event_data[0]
            event_args = event_data[1] if len(event_data) > 1 else None
            _LOGGER.debug(
                "Event on %s: %s -> %s", namespace, event_name, event_args
            )
            await self._emit_handlers(event_name, namespace, event_args)
        else:
            _LOGGER.debug("Unexpected event format: %s", event_data)

    async def _emit_handlers(
        self, event: str, namespace: str, data: Any
    ) -> None:
        """Call registered handlers for an event."""
        ns_handlers = self._event_handlers.get(namespace, {})
        handlers = ns_handlers.get(event, []) + ns_handlers.get("*", [])
        for handler in handlers:
            try:
                result = handler(event, data) if event == "*" or event not in ns_handlers else handler(data)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                _LOGGER.exception(
                    "Error in handler for event %s on %s", event, namespace
                )

    async def emit(
        self, event: str, data: Any, namespace: str = "/"
    ) -> None:
        """Send an event to the server."""
        if not self.connected:
            raise DknConnectionError("Socket not connected")

        payload = json.dumps([event, data])

        if namespace and namespace != "/":
            message = f"{EIO_MESSAGE}{SIO_EVENT}{namespace},{payload}"
        else:
            message = f"{EIO_MESSAGE}{SIO_EVENT}{payload}"

        _LOGGER.debug("Sending: %s", message)
        await self._ws.send_str(message)

    async def _ping_loop(self) -> None:
        """Send periodic pings to keep connection alive."""
        try:
            while self._connected:
                await asyncio.sleep(self._ping_interval)
                if self._ws and not self._ws.closed:
                    await self._ws.send_str(EIO_PING)
                    _LOGGER.debug("Sent EIO ping")
                else:
                    break
        except asyncio.CancelledError:
            return
        except Exception:
            _LOGGER.exception("Error in ping loop")

    async def disconnect(self) -> None:
        """Disconnect the websocket."""
        self._connected = False
        if self._ping_task:
            self._ping_task.cancel()
        if self._listener_task:
            self._listener_task.cancel()
        if self._ws and not self._ws.closed:
            await self._ws.close()


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
        self._sio_connections: dict[str, SocketIOv2Connection] = {}
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
                    self.refresh_token = data.get(
                        "refreshToken", self.refresh_token
                    )
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
            raise DknConnectionError(
                f"Connection error during login: {err}"
            ) from err

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
                        "units": install.get("units", 0),
                        "data": device,
                    }
                    _LOGGER.debug(
                        "Device %s (%s) data keys: %s",
                        device.get("name"),
                        mac,
                        list(device.keys()),
                    )

    async def connect_socket(self) -> None:
        """Establish socket.io connections for real-time updates."""
        if not self.token:
            raise DknAuthError("Must authenticate before connecting socket")

        session = await self._ensure_session()

        # Connect to /users namespace
        users_conn = SocketIOv2Connection(
            self._base_url, API_SOCKET_PATH, self.token, session
        )
        users_conn.on("connect", "/users", self._on_users_connect)
        users_conn.on("disconnect", "/users", self._on_users_disconnect)
        try:
            await users_conn.connect(["/users"])
            self._sio_connections["users"] = users_conn
            _LOGGER.debug("Connected to /users namespace")
        except DknConnectionError:
            _LOGGER.error("Failed to connect to /users namespace")
            raise

        # Connect to each installation namespace
        for install in self._installations:
            install_id = install["_id"]
            namespace = f"/{install_id}::dknUsa"

            conn = SocketIOv2Connection(
                self._base_url, API_SOCKET_PATH, self.token, session
            )
            conn.on(EVENT_DEVICE_DATA, namespace, self._on_device_data)
            conn.on("connect", namespace, self._on_install_connect)
            conn.on("disconnect", namespace, self._on_install_disconnect)
            try:
                await conn.connect([namespace])
                self._sio_connections[install_id] = conn
                _LOGGER.debug("Connected to namespace %s", namespace)
            except DknConnectionError:
                _LOGGER.error("Failed to connect to namespace %s", namespace)
                raise

        self._connected = True
        _LOGGER.info("All socket.io connections established")

    def _on_users_connect(self, data: Any) -> None:
        _LOGGER.debug("Users namespace connected")

    def _on_users_disconnect(self, data: Any) -> None:
        _LOGGER.warning("Users namespace disconnected")

    def _on_install_connect(self, data: Any) -> None:
        _LOGGER.debug("Installation namespace connected")

    def _on_install_disconnect(self, data: Any) -> None:
        _LOGGER.warning("Installation namespace disconnected")

    def _on_device_data(self, data: Any) -> None:
        """Handle incoming device data from socket.io."""
        if not isinstance(data, dict):
            _LOGGER.debug("Unexpected device-data format: %s", data)
            return

        mac = data.get("mac")
        if mac and mac in self._devices:
            device = self._devices[mac]
            device["data"].update(data)
            _LOGGER.debug("Device data update for %s: %s", mac, data)
            self._notify_device_update(mac)
        else:
            _LOGGER.debug("Device data for unknown mac %s: %s", mac, data)

    async def send_command(
        self, mac: str, prop: str, value: Any
    ) -> None:
        """Send a command to a device."""
        device = self._devices.get(mac)
        if not device:
            raise DknCloudApiError(f"Unknown device: {mac}")

        installation_id = device["installation_id"]
        conn = self._sio_connections.get(installation_id)

        if not conn or not conn.connected:
            _LOGGER.warning(
                "Socket not connected for installation %s, reconnecting",
                installation_id,
            )
            await self._reconnect_installation(installation_id)
            conn = self._sio_connections.get(installation_id)
            if not conn or not conn.connected:
                raise DknConnectionError(
                    f"Cannot connect to installation {installation_id}"
                )

        namespace = f"/{installation_id}::dknUsa"
        await conn.emit(
            EVENT_CREATE_MACHINE_EVENT,
            {"mac": mac, "property": prop, "value": value},
            namespace=namespace,
        )
        _LOGGER.debug("Sent command to %s: %s=%s", mac, prop, value)

        # Optimistically update local state
        device["data"][prop] = value
        self._notify_device_update(mac)

    async def _reconnect_installation(self, installation_id: str) -> None:
        """Reconnect to an installation namespace."""
        old_conn = self._sio_connections.pop(installation_id, None)
        if old_conn:
            await old_conn.disconnect()

        session = await self._ensure_session()
        namespace = f"/{installation_id}::dknUsa"
        conn = SocketIOv2Connection(
            self._base_url, API_SOCKET_PATH, self.token, session
        )
        conn.on(EVENT_DEVICE_DATA, namespace, self._on_device_data)
        conn.on("connect", namespace, self._on_install_connect)
        conn.on("disconnect", namespace, self._on_install_disconnect)
        await conn.connect([namespace])
        self._sio_connections[installation_id] = conn

    def register_device_callback(self, callback: Callable) -> Callable:
        """Register a callback for device updates. Returns unregister function."""
        self._device_callbacks.append(callback)

        def unregister() -> None:
            if callback in self._device_callbacks:
                self._device_callbacks.remove(callback)

        return unregister

    def _notify_device_update(self, mac: str | None = None) -> None:
        """Notify all registered callbacks of device updates."""
        for cb in self._device_callbacks:
            try:
                cb(mac)
            except Exception:
                _LOGGER.exception("Error in device update callback")

    async def disconnect(self) -> None:
        """Disconnect all connections and close session."""
        for key, conn in self._sio_connections.items():
            try:
                await conn.disconnect()
            except Exception:
                _LOGGER.debug("Error disconnecting socket %s", key)
        self._sio_connections.clear()
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
