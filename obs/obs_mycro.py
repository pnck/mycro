"""MYCRO OBS capture provider script.

A companion client for the MYCRO device (default :7373, protocol v1; docs/protocol.md).
Provides pure in-memory screen capture via OBS-WebSocket v5 with zero disk I/O,
direct LAN preview streaming, and synchronous telemetry sampling.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import io
import json
import os
import platform
import socket
import struct
import threading
import time
import urllib.parse
from typing import Callable, Optional, Tuple, List, Dict, Any

try:
    import obspython as obs
except ImportError:
    obs = None


# ==============================================================================
# Module Constants (Zero Magic Numbers)
# ==============================================================================

DEFAULT_DEVICE_HOST = "mycro.local"
DEFAULT_RAW_PORT = 7373
DEFAULT_WS_PORT = 4455
PROTO_VERSION = 1
AUTH_MAGIC = b"MYCR"
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

# Timeouts (in seconds)
WORKER_RECONNECT_INTERVAL_S = 3.0
WORKER_SHUTDOWN_TIMEOUT_S = 2.0
PREVIEW_LEASE_TTL_S = 5.0
RPC_RESPONSE_TIMEOUT_S = 5.0
RPC_HANDSHAKE_TIMEOUT_S = 3.0
WIRE_CONNECT_TIMEOUT_S = 3.0
WIRE_IO_TIMEOUT_S = 1.0
UI_CONNECT_LATCH_S = 1.2

# Mirror of spec/vm.md EXT_TIMEOUT_MS. Reserve one bounded wire write for the
# unwatch response instead of extending the requester's deadline arbitrarily.
DEVICE_EXT_TIMEOUT_MS = 5000
DEVICE_WORK_TIMEOUT_S = DEVICE_EXT_TIMEOUT_MS / 1000.0 - WIRE_IO_TIMEOUT_S
UNWATCH_STOP_TIMEOUT_S = DEVICE_WORK_TIMEOUT_S

# Buffers, Bounds and Limits
MAX_CHUNK_PAYLOAD = 8188  # 8192 bytes protocol cap minus 4-byte (<HH) stream header
MAX_FRAME_PAYLOAD = 65536  # Upper bound on Protocol v1 framed TCP payload
MAX_POINTS = 256
MAX_RECT_EDGE = 128
MAX_HTTP_HEADER_BYTES = 4096
IO_BUFFER_SIZE = 65536
TELEMETRY_MIN_INTERVAL_MS = 20

# Log levels
LOG_INFO = 1
LOG_WARNING = 2
LOG_ERROR = 3

# UI/Main thread cached active scene
_cached_scene_name = ""


def get_current_scene_name() -> str:
    """Safely inspects active program scene on the OBS main thread."""
    if obs is not None and hasattr(obs, "obs_frontend_get_current_scene"):
        scene = obs.obs_frontend_get_current_scene()
        if scene:
            try:
                name = obs.obs_source_get_name(scene)
                return name or ""
            finally:
                obs.obs_source_release(scene)
    return ""


def log_msg(level: int, msg: str) -> None:
    """Logs message to OBS script console if available, otherwise prints."""
    if obs is not None and hasattr(obs, "script_log"):
        obs.script_log(level, msg)
    else:
        print(f"[MYCRO-OBS] {msg}")


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("Operation deadline expired")
    return remaining


# ==============================================================================
# Exceptions
# ==============================================================================

class ObsWsError(Exception):
    """Base exception for OBS WebSocket operations."""


class ObsWsTransportError(ObsWsError):
    """Transport-level failure: socket error, framing corruption, EOF, or deadline timeout."""


class ObsWsBusinessError(ObsWsError):
    """Application-level RPC failure returned by OBS in op:7 (requestStatus.result == false)."""

    def __init__(self, code: int, comment: str):
        self.code = code
        self.comment = comment
        super().__init__(f"OBS RPC error ({code}): {comment}")


# ==============================================================================
# 1. OBS WebSocket Config Auto-Discovery
# ==============================================================================

class ObsWsConfig:
    """Discovers local obs-websocket plugin configuration across platforms."""

    @classmethod
    def get_config_path(cls) -> str:
        system = platform.system()
        if system == "Darwin":
            base = os.path.expanduser("~/Library/Application Support/obs-studio")
        elif system == "Windows":
            base = os.path.join(os.environ.get("APPDATA", ""), "obs-studio")
        else:
            base = os.path.expanduser("~/.config/obs-studio")
        return os.path.join(base, "plugin_config", "obs-websocket", "config.json")

    @classmethod
    def load(cls) -> Tuple[bool, int, str]:
        """Returns (enabled, port, password)."""
        path = cls.get_config_path()
        if not os.path.exists(path):
            return False, DEFAULT_WS_PORT, ""
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            enabled = bool(data.get("server_enabled", False))
            port = int(data.get("server_port", DEFAULT_WS_PORT))
            pwd = str(data.get("server_password", ""))
            return enabled, port, pwd
        except Exception:
            return False, DEFAULT_WS_PORT, ""

    @classmethod
    def get_configured_script_source(cls) -> str:
        """Inspects local OBS active scene collection JSON to find user-configured script source."""
        system = platform.system()
        if system == "Darwin":
            base = os.path.expanduser("~/Library/Application Support/obs-studio")
        elif system == "Windows":
            base = os.path.join(os.environ.get("APPDATA", ""), "obs-studio")
        else:
            base = os.path.expanduser("~/.config/obs-studio")
        scenes_dir = os.path.join(base, "basic", "scenes")
        if not os.path.isdir(scenes_dir):
            return ""
        for fn in os.listdir(scenes_dir):
            if fn.endswith(".json") and not fn.endswith(".bak"):
                fp = os.path.join(scenes_dir, fn)
                try:
                    with open(fp, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    scripts = data.get("modules", {}).get("scripts-tool", [])
                    for sc in scripts:
                        p = str(sc.get("path", "")).lower()
                        if "obs_mycro" in p:
                            src = str(sc.get("settings", {}).get("source", "")).strip()
                            if src:
                                return src
                except Exception:
                    continue
        return ""


# ==============================================================================
# 2. Pure Standard-Library RFC 6455 Client & OBS-WS v5 State Machine
# ==============================================================================

class ObsWsClient:
    """Lightweight RFC 6455 WebSocket client tailored for obs-websocket v5 RPC.

    Uses sock.makefile('rb') for standard buffered stream parsing to prevent
    dropping the initial WebSocket frame arriving in the same TCP segment as
    the HTTP 101 Switching Protocols response headers.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = DEFAULT_WS_PORT, password: str = ""):
        self.host = host
        self.port = port
        self.password = password
        self.sock: Optional[socket.socket] = None
        self.rfile: Optional[io.BufferedReader] = None
        self._lock = threading.Lock()
        self._sock_lock = threading.Lock()
        self._req_counter = 0

    def connect(self, timeout: float = RPC_HANDSHAKE_TIMEOUT_S, deadline: Optional[float] = None) -> None:
        """Establishes TCP connection, performs RFC 6455 handshake, and authenticates."""
        if deadline is None:
            deadline = time.monotonic() + timeout
        self.disconnect()
        try:
            s = socket.create_connection((self.host, self.port), timeout=min(timeout, _remaining(deadline)))
            s.settimeout(_remaining(deadline))
            rf = s.makefile("rb", buffering=IO_BUFFER_SIZE)
            with self._sock_lock:
                self.sock = s
                self.rfile = rf
        except Exception as e:
            raise ObsWsTransportError(f"Failed to connect to OBS WebSocket at {self.host}:{self.port}: {e}") from e

        try:
            # RFC 6455 Handshake
            raw_key = os.urandom(16)
            sec_key = base64.b64encode(raw_key).decode("ascii")
            req = (
                f"GET / HTTP/1.1\r\n"
                f"Host: {self.host}:{self.port}\r\n"
                f"Upgrade: websocket\r\n"
                f"Connection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {sec_key}\r\n"
                f"Sec-WebSocket-Version: 13\r\n\r\n"
            )
            s.settimeout(_remaining(deadline))
            s.sendall(req.encode("ascii"))

            # Stream-parse HTTP status line and headers line-by-line
            s.settimeout(_remaining(deadline))
            status_line = rf.readline(MAX_HTTP_HEADER_BYTES)
            if not status_line:
                raise ObsWsTransportError("Server closed connection during WebSocket handshake")
            if b"101" not in status_line:
                raise ObsWsTransportError(f"WebSocket upgrade rejected: {status_line.decode('latin1', 'replace').strip()}")

            headers: Dict[str, str] = {}
            total_header_bytes = len(status_line)
            while True:
                s.settimeout(_remaining(deadline))
                line = rf.readline(MAX_HTTP_HEADER_BYTES)
                if not line:
                    raise ObsWsTransportError("Unexpected EOF while reading HTTP upgrade headers")
                total_header_bytes += len(line)
                if total_header_bytes > MAX_HTTP_HEADER_BYTES:
                    raise ObsWsTransportError("HTTP upgrade headers exceeded maximum size cap")
                if line in (b"\r\n", b"\n", b""):
                    break
                line_str = line.decode("latin1", errors="replace").strip()
                if ":" in line_str:
                    k, v = line_str.split(":", 1)
                    headers[k.strip().lower()] = v.strip()

            # Verify Sec-WebSocket-Accept
            expected_accept = base64.b64encode(
                hashlib.sha1((sec_key + WS_GUID).encode("ascii")).digest()
            ).decode("ascii")
            if headers.get("sec-websocket-accept") != expected_accept:
                raise ObsWsTransportError("Invalid Sec-WebSocket-Accept returned from server")

            # obs-websocket v5 Identify negotiation
            hello_env = self._recv_frame_json(deadline)
            if hello_env.get("op") != 0:
                raise ObsWsTransportError(f"Expected op:0 (Hello), got {hello_env}")

            d = hello_env.get("d", {})
            auth_req = d.get("authentication")
            identify_d: Dict[str, Any] = {
                "rpcVersion": 1,
                "eventSubscriptions": 0,  # Explicitly unsubscribe from all server event spam
            }

            if auth_req:
                challenge = auth_req.get("challenge", "")
                salt = auth_req.get("salt", "")
                secret = base64.b64encode(
                    hashlib.sha256((self.password + salt).encode("utf-8")).digest()
                ).decode("ascii")
                auth_hash = base64.b64encode(
                    hashlib.sha256((secret + challenge).encode("utf-8")).digest()
                ).decode("ascii")
                identify_d["authentication"] = auth_hash

            self._send_frame_json({"op": 1, "d": identify_d}, deadline)
            ident_env = self._recv_frame_json(deadline)
            if ident_env.get("op") != 2:
                raise ObsWsTransportError(f"Authentication failed: {ident_env}")

            # Clear handshake timeout; per-call RPC deadlines govern subsequent calls
            s.settimeout(None)
        except Exception:
            self.disconnect()
            raise

    def disconnect(self) -> None:
        """Locklessly tears down socket and stream, breaking any in-flight recv in 0ms."""
        with self._sock_lock:
            s = self.sock
            rf = self.rfile
            self.sock = None
            self.rfile = None
        if s:
            try:
                s.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            try:
                s.close()
            except Exception:
                pass
        if rf:
            try:
                rf.close()
            except Exception:
                pass

    def call(
        self,
        request_type: str,
        request_data: Optional[dict] = None,
        deadline: Optional[float] = None,
    ) -> dict:
        """Run one RPC inside the caller's absolute deadline, including queueing."""
        if deadline is None:
            deadline = time.monotonic() + RPC_RESPONSE_TIMEOUT_S
        try:
            acquired = self._lock.acquire(timeout=_remaining(deadline))
        except TimeoutError as e:
            raise ObsWsTransportError(f"OBS RPC call '{request_type}' expired before queueing") from e
        if not acquired:
            raise ObsWsTransportError(f"OBS RPC call '{request_type}' expired waiting for another call")

        try:
            with self._sock_lock:
                s = self.sock
                rf = self.rfile
            if not s or not rf:
                raise ObsWsTransportError("ObsWsClient is not connected")
            self._req_counter += 1
            req_id = f"mycro-{self._req_counter}"
            try:
                self._send_frame_json({
                    "op": 6,
                    "d": {
                        "requestType": request_type,
                        "requestId": req_id,
                        "requestData": request_data or {},
                    }
                }, deadline)

                while True:
                    env = self._recv_frame_json(deadline)
                    op = env.get("op")
                    if op == 7:  # RequestResponse
                        d = env.get("d", {})
                        if d.get("requestId") == req_id:
                            status = d.get("requestStatus", {})
                            if not status.get("result", False):
                                raise ObsWsBusinessError(
                                    status.get("code", 0),
                                    status.get("comment", "unknown"),
                                )
                            return d.get("responseData", {})
            except ObsWsBusinessError:
                # Business error: application level failure. Socket remains synchronized and intact.
                raise
            except Exception as e:
                # Transport-level failure: socket closed to prevent desync
                self.disconnect()
                raise ObsWsTransportError(f"Transport error during OBS RPC call '{request_type}': {e}") from e
        finally:
            self._lock.release()

    def _send_frame(self, opcode: int, payload: bytes, deadline: Optional[float] = None) -> None:
        """Packs and sends a client-to-server masked RFC 6455 frame."""
        with self._sock_lock:
            s = self.sock
        if not s:
            raise ObsWsTransportError("Socket not open")
        length = len(payload)
        mask = os.urandom(4)

        if length <= 125:
            b1 = 0x80 | length
            hdr = struct.pack("!BB", 0x80 | (opcode & 0x0F), b1) + mask
        elif length <= 65535:
            b1 = 0x80 | 126
            hdr = struct.pack("!BBH", 0x80 | (opcode & 0x0F), b1, length) + mask
        else:
            b1 = 0x80 | 127
            hdr = struct.pack("!BBQ", 0x80 | (opcode & 0x0F), b1, length) + mask

        masked_payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        if deadline is not None:
            s.settimeout(_remaining(deadline))
        s.sendall(hdr + masked_payload)

    def _send_frame_json(self, data: dict, deadline: Optional[float] = None) -> None:
        raw = json.dumps(data, separators=(",", ":")).encode("utf-8")
        self._send_frame(opcode=1, payload=raw, deadline=deadline)

    def _recv_exact(self, n: int, deadline: Optional[float] = None) -> bytes:
        with self._sock_lock:
            rf = self.rfile
            s = self.sock
        if not rf:
            raise ObsWsTransportError("Stream not open")
        if deadline is not None:
            s.settimeout(_remaining(deadline))
        data = rf.read(n)
        if len(data) < n:
            raise ObsWsTransportError("Unexpected EOF reading WebSocket stream")
        return data

    def _recv_frame(self, deadline: Optional[float] = None) -> Tuple[int, bytes]:
        """Reads one complete RFC 6455 message, echoing Pings and joining fragments."""
        msg_payload = bytearray()
        initial_opcode = -1

        while True:
            b0, b1 = struct.unpack("!BB", self._recv_exact(2, deadline))
            fin = (b0 & 0x80) != 0
            opcode = b0 & 0x0F
            masked = (b1 & 0x80) != 0
            payload_len = b1 & 0x7F

            if payload_len == 126:
                payload_len = struct.unpack("!H", self._recv_exact(2, deadline))[0]
            elif payload_len == 127:
                payload_len = struct.unpack("!Q", self._recv_exact(8, deadline))[0]

            mask = self._recv_exact(4, deadline) if masked else b""
            raw_payload = self._recv_exact(payload_len, deadline)
            if masked:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(raw_payload))
            else:
                payload = raw_payload

            # RFC 6455 Control frames (Ping / Pong / Close): must be FIN and length <= 125
            if opcode & 0x8:
                if not fin or payload_len > 125:
                    raise ObsWsTransportError(
                        f"Malformed RFC 6455 control frame: opcode={opcode}, fin={fin}, len={payload_len}"
                    )
                if opcode == 0x9:  # Ping: echo exact payload back in Pong
                    self._send_frame(opcode=0xA, payload=payload, deadline=deadline)
                    continue
                if opcode == 0xA:  # Pong: ignore
                    continue
                if opcode == 0x8:  # Close
                    raise ObsWsTransportError("WebSocket connection closed by remote peer")

            # Data frames (Text / Binary / Continuation)
            if opcode != 0x0:
                initial_opcode = opcode
            msg_payload.extend(payload)

            if fin:
                return initial_opcode, bytes(msg_payload)

    def _recv_frame_json(self, deadline: Optional[float] = None) -> dict:
        opcode, payload = self._recv_frame(deadline)
        if opcode != 1:  # Text frame
            raise ObsWsTransportError(f"Expected text frame (opcode 1), got {opcode}")
        try:
            return json.loads(payload.decode("utf-8"))
        except Exception as e:
            raise ObsWsTransportError(f"Malformed JSON in WebSocket text frame: {e}") from e


# ==============================================================================
# 3. Orthogonal Atomic Capability: CaptureEngine
# ==============================================================================

class CaptureEngine:
    """Screen capture and pixel extraction engine bound to the plugin-owned source."""

    def __init__(self, ws_client: ObsWsClient, default_source: str = ""):
        self.ws = ws_client
        self.source_name = default_source

    def update_source(self, source_name: str) -> None:
        self.source_name = source_name

    def resolve_source(self, deadline: Optional[float] = None) -> str:
        """Resolves target capture source.

        Priority order:
        1. Explicitly configured source_name
        2. User-configured script source from OBS scene collection JSON
        3. Active Program Scene's screen/display capture input (GetSceneItemList)
        4. First enabled input in active Program Scene
        5. First available screen/display capture input from GetInputList
        6. Active Program Scene or cached scene name
        """
        if deadline is None:
            deadline = time.monotonic() + RPC_RESPONSE_TIMEOUT_S
        if self.source_name:
            return self.source_name

        saved_source = ObsWsConfig.get_configured_script_source()
        if saved_source:
            return saved_source

        if self.ws is not None:
            first_enabled = ""
            scene_name = ""
            try:
                scene_name = _cached_scene_name
                if not scene_name:
                    resp = self.ws.call("GetCurrentProgramScene", deadline=deadline)
                    scene_name = resp.get("currentProgramSceneName") or resp.get("sceneName", "")
                if scene_name:
                    items = self.ws.call("GetSceneItemList", {"sceneName": scene_name}, deadline=deadline).get("sceneItems", [])
                    for it in items:
                        if not it.get("sceneItemEnabled", True):
                            continue
                        name = it.get("sourceName", "")
                        kind_lower = str(it.get("inputKind", "")).lower()
                        if any(k in kind_lower for k in ("screen", "display", "window", "monitor")):
                            return name
                        if not first_enabled:
                            first_enabled = name
            except Exception:
                pass

            try:
                inputs = self.ws.call("GetInputList", deadline=deadline).get("inputs", [])
                for inp in inputs:
                    kind_lower = str(inp.get("inputKind", "")).lower()
                    if any(k in kind_lower for k in ("screen", "display", "window", "monitor")):
                        return inp.get("inputName", "")
            except Exception:
                pass

            if first_enabled:
                return first_enabled

            if scene_name:
                return scene_name

        if _cached_scene_name:
            return _cached_scene_name

        return ""

    def get_dimensions(self, deadline: Optional[float] = None) -> Tuple[int, int]:
        """Returns (width, height) with 0ms overhead, never taking a screenshot."""
        target = self.source_name or _cached_scene_name
        if obs is not None and target:
            source = obs.obs_get_source_by_name(target)
            if source:
                try:
                    w = obs.obs_source_get_width(source)
                    h = obs.obs_source_get_height(source)
                    if w > 0 and h > 0:
                        return w, h
                finally:
                    obs.obs_source_release(source)
        try:
            settings = self.ws.call("GetVideoSettings", deadline=deadline)
            return int(settings.get("baseWidth", 1920)), int(settings.get("baseHeight", 1080))
        except Exception:
            return 1920, 1080

    def _fetch_screenshot_bytes(
        self,
        fmt: str,
        source_name: str = "",
        width: int = -1,
        height: int = -1,
        deadline: Optional[float] = None,
    ) -> bytes:
        if deadline is None:
            deadline = time.monotonic() + RPC_RESPONSE_TIMEOUT_S
        source = source_name or self.resolve_source(deadline)
        if not source:
            raise ValueError("No capture source or active scene available in OBS")
        req_data: Dict[str, Any] = {
            "sourceName": source,
            "imageFormat": fmt,
        }
        if isinstance(width, int) and isinstance(height, int) and width > 0 and height > 0:
            req_data["imageWidth"] = width
            req_data["imageHeight"] = height

        try:
            resp = self.ws.call("GetSourceScreenshot", req_data, deadline=deadline)
        except ObsWsTransportError:
            if time.monotonic() >= deadline:
                raise
            self.ws.connect(timeout=min(RPC_HANDSHAKE_TIMEOUT_S, _remaining(deadline)), deadline=deadline)
            resp = self.ws.call("GetSourceScreenshot", req_data, deadline=deadline)
        uri = resp.get("imageData", "")
        comma = uri.find(",")
        raw_b64 = uri[comma + 1:] if comma != -1 else uri
        return base64.b64decode(raw_b64)

    def get_raw_bmp(
        self,
        source_name: str = "",
        width: int = -1,
        height: int = -1,
        deadline: Optional[float] = None,
    ) -> memoryview:
        """Fetches uncompressed BMP via WebSocket in-memory with optional GPU scaling."""
        return memoryview(self._fetch_screenshot_bytes(
            "bmp", source_name=source_name, width=width, height=height, deadline=deadline,
        ))

    def get_raw_png(self, source_name: str = "", deadline: Optional[float] = None) -> bytes:
        """Fetches pure-memory PNG bytes for direct browser preview."""
        return self._fetch_screenshot_bytes("png", source_name=source_name, deadline=deadline)

    @staticmethod
    def parse_bmp_header(bmp: memoryview) -> Tuple[int, int, int, int]:
        """Validates 24-bit uncompressed BMP and returns (offset, width, height, stride)."""
        bmp_len = len(bmp)
        if bmp_len < 54 or bmp[:2] != b"BM":
            raise ValueError("Invalid BMP signature or truncated header")
        offset = struct.unpack_from("<I", bmp, 10)[0]
        dib_size = struct.unpack_from("<I", bmp, 14)[0]
        if offset < 54 or offset >= bmp_len or dib_size < 40:
            raise ValueError(f"Invalid BMP offset ({offset}) or DIB size ({dib_size})")
        w, h = struct.unpack_from("<ii", bmp, 18)
        bpp = struct.unpack_from("<H", bmp, 28)[0]
        comp = struct.unpack_from("<I", bmp, 30)[0]
        if bpp != 24 or comp != 0:
            raise ValueError(f"Expected uncompressed 24-bit BMP, got bpp={bpp}, comp={comp}")
        if w <= 0 or h == 0:
            raise ValueError(f"Invalid BMP dimensions: {w}x{h}")
        stride = ((w * 3 + 3) // 4) * 4
        if offset + stride * abs(h) > bmp_len:
            raise ValueError("BMP pixel array truncated")
        return offset, w, h, stride

    def extract_points(self, bmp: memoryview, points: List[Tuple[int, int]]) -> List[int]:
        """O(1) memoryview buffer pointer arithmetic for exact point color lookup."""
        offset, width, height, stride = self.parse_bmp_header(bmp)
        colors = []
        is_bottom_up = height > 0
        abs_h = abs(height)

        for x, y in points:
            if not (0 <= x < width and 0 <= y < abs_h):
                raise ValueError(f"Point ({x}, {y}) out of source bounds ({width}x{abs_h})")
            row = (height - 1 - y) if is_bottom_up else y
            pos = offset + row * stride + x * 3
            b = bmp[pos]
            g = bmp[pos + 1]
            r = bmp[pos + 2]
            colors.append((r << 16) | (g << 8) | b)
        return colors

    def extract_rect(self, bmp: memoryview, rect: Dict[str, int]) -> bytes:
        """Lossless region crop into raw RGB8 bytes using row-by-row memoryview slicing."""
        offset, src_w, src_h, stride = self.parse_bmp_header(bmp)
        abs_h = abs(src_h)
        rx, ry = rect["x"], rect["y"]
        rw, rh = rect["w"], rect["h"]

        if rx < 0 or ry < 0 or rw <= 0 or rh <= 0 or (rx + rw) > src_w or (ry + rh) > abs_h:
            raise ValueError(f"Crop rect {rect} out of source bounds ({src_w}x{abs_h})")

        out = bytearray(rw * rh * 3)
        out_idx = 0
        is_bottom_up = src_h > 0

        for row_idx in range(rh):
            curr_y = ry + row_idx
            src_row = (src_h - 1 - curr_y) if is_bottom_up else curr_y
            row_start = offset + src_row * stride + rx * 3
            row_end = row_start + rw * 3
            bgr_span = bmp[row_start:row_end]
            for i in range(0, len(bgr_span), 3):
                out[out_idx] = bgr_span[i + 2]      # R
                out[out_idx + 1] = bgr_span[i + 1]  # G
                out[out_idx + 2] = bgr_span[i]      # B
                out_idx += 3
        return bytes(out)


# ==============================================================================
# 4. Orthogonal Atomic Capability: EphemeralHttpServer (Asyncio-based)
# ==============================================================================

class EphemeralHttpServer:
    """Asyncio-based ephemeral HTTP server with cross-connection Chrome PNA and strict lease."""

    def __init__(self):
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._server: Optional[asyncio.Server] = None
        self._shutdown_event: Optional[asyncio.Event] = None
        self._done_event = threading.Event()
        self._stop_requested = threading.Event()
        self._lock = threading.Lock()

    def serve(
        self,
        token: str,
        nonce: str,
        data_producer: Callable[[], bytes],
        ttl_sec: float = PREVIEW_LEASE_TTL_S,
        start_deadline: Optional[float] = None,
    ) -> int:
        """Starts an asyncio ephemeral HTTP server on 0.0.0.0:0 in a background thread."""
        start_timeout = ttl_sec
        if start_deadline is not None:
            start_timeout = min(ttl_sec, max(0, start_deadline - time.monotonic()))
        self.stop(timeout_sec=start_timeout if start_deadline is not None else WORKER_SHUTDOWN_TIMEOUT_S)
        if self._thread and self._thread.is_alive():
            raise RuntimeError("Previous preview server did not stop before replacement")

        port_holder: List[int] = []
        started_event = threading.Event()
        self._done_event.clear()
        self._stop_requested.clear()

        def _thread_target():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop
            try:
                loop.run_until_complete(
                    self._serve_async(token, nonce, data_producer, ttl_sec, port_holder, started_event)
                )
            except (asyncio.CancelledError, KeyboardInterrupt, RuntimeError):
                pass
            except Exception as e:
                log_msg(LOG_WARNING, f"Ephemeral server ended: {e}")
            finally:
                try:
                    pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
                    for t in pending:
                        t.cancel()
                    if pending:
                        loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                    loop.close()
                except Exception:
                    pass
                self._done_event.set()

        self._thread = threading.Thread(target=_thread_target, daemon=True, name="mycro-ephemeral-async")
        self._thread.start()

        wait_timeout = start_timeout
        if start_deadline is not None:
            wait_timeout = min(ttl_sec, max(0, start_deadline - time.monotonic()))
        if not started_event.wait(timeout=wait_timeout) or not port_holder:
            self._stop_requested.set()
            stop_timeout = start_timeout if start_deadline is None else max(0, start_deadline - time.monotonic())
            self.stop(timeout_sec=stop_timeout)
            raise RuntimeError("Failed to start ephemeral HTTP server within its lease")
        return port_holder[0]

    async def _serve_async(
        self,
        token: str,
        nonce: str,
        data_producer: Callable[[], bytes],
        ttl_sec: float,
        port_holder: List[int],
        started_event: threading.Event,
    ) -> None:
        if self._stop_requested.is_set():
            return

        expected_sig = hmac.new(
            (token or "").encode("utf-8"),
            nonce.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        close_after_serve = asyncio.Event()
        shutdown_event = asyncio.Event()
        self._shutdown_event = shutdown_event
        served = False
        client_tasks = set()
        loop = asyncio.get_running_loop()
        lease_deadline = loop.time() + ttl_sec

        def _handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
            task = asyncio.create_task(_process_client(reader, writer))
            client_tasks.add(task)
            task.add_done_callback(client_tasks.discard)

        async def _process_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
            nonlocal served
            try:
                req_data = bytearray()
                while b"\r\n\r\n" not in req_data:
                    chunk = await reader.read(1024)
                    if not chunk:
                        break
                    req_data.extend(chunk)
                    if len(req_data) > MAX_HTTP_HEADER_BYTES:
                        writer.write(b"HTTP/1.1 413 Payload Too Large\r\nConnection: close\r\n\r\n")
                        await writer.drain()
                        writer.close()
                        return

                if not req_data:
                    writer.close()
                    return

                header_text = req_data.decode("latin1", errors="replace")
                lines = header_text.split("\r\n")
                if not lines:
                    writer.close()
                    return
                parts = lines[0].split()
                if len(parts) < 2:
                    writer.close()
                    return
                method, full_path = parts[0].upper(), parts[1]

                cors_headers = (
                    "Access-Control-Allow-Origin: *\r\n"
                    "Access-Control-Allow-Methods: GET, OPTIONS\r\n"
                    "Access-Control-Allow-Headers: *\r\n"
                    "Access-Control-Allow-Private-Network: true\r\n"
                )

                if method == "OPTIONS":
                    # Chrome PNA Preflight response (allows separate connection for actual GET)
                    resp = (
                        f"HTTP/1.1 204 No Content\r\n"
                        f"{cors_headers}"
                        f"Connection: close\r\n\r\n"
                    ).encode("ascii")
                    writer.write(resp)
                    await writer.drain()
                    writer.close()
                    return

                if method == "GET":
                    parsed = urllib.parse.urlparse(full_path)
                    qs = urllib.parse.parse_qs(parsed.query)
                    req_nonce = qs.get("nonce", [""])[0]
                    sig = qs.get("sig", [""])[0]

                    if req_nonce != nonce or not hmac.compare_digest(sig, expected_sig):
                        writer.write(b"HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\nForbidden")
                        await writer.drain()
                        writer.close()
                        return

                    if served:
                        writer.write(b"HTTP/1.1 410 Gone\r\nConnection: close\r\n\r\n")
                        await writer.drain()
                        writer.close()
                        return
                    served = True

                    # Strict Lazy Capture contract: call producer only after valid HMAC authentication
                    raw_body = data_producer()
                    hdr = (
                        f"HTTP/1.1 200 OK\r\n"
                        f"Content-Type: image/png\r\n"
                        f"Content-Length: {len(raw_body)}\r\n"
                        f"{cors_headers}"
                        f"Connection: close\r\n\r\n"
                    ).encode("ascii")
                    writer.write(hdr + raw_body)
                    await writer.drain()
                    writer.close()
                    # Single-use fulfilled: schedule server shutdown
                    close_after_serve.set()
                    return

                writer.write(b"HTTP/1.1 405 Method Not Allowed\r\nConnection: close\r\n\r\n")
                await writer.drain()
                writer.close()
            except Exception:
                try:
                    writer.close()
                except Exception:
                    pass

        server = await asyncio.start_server(_handle_client, "0.0.0.0", 0)
        self._server = server
        if self._stop_requested.is_set():
            server.close()
            await server.wait_closed()
            self._server = None
            return

        port = server.sockets[0].getsockname()[1]
        port_holder.append(port)
        started_event.set()

        try:
            # Bound total lifespan by PREVIEW_LEASE_TTL_S, single GET completion, or external shutdown
            serve_task = asyncio.create_task(server.serve_forever())
            wait_close_task = asyncio.create_task(close_after_serve.wait())
            wait_shutdown_task = asyncio.create_task(shutdown_event.wait())
            _, pending = await asyncio.wait(
                [serve_task, wait_close_task, wait_shutdown_task],
                timeout=max(0, lease_deadline - loop.time()),
                return_when=asyncio.FIRST_COMPLETED,
            )
            for p in pending:
                p.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
        finally:
            server.close()
            await server.wait_closed()
            self._server = None
            if close_after_serve.is_set() and not shutdown_event.is_set() and client_tasks:
                # A fulfilled lease stops new accepts, not requests already in
                # flight. Let them finish their 200/410 responses within the
                # same lease; TTL or external shutdown still cancels immediately.
                try:
                    await asyncio.wait_for(
                        asyncio.gather(*client_tasks),
                        max(0, lease_deadline - loop.time()),
                    )
                except asyncio.TimeoutError:
                    pass
            for task in client_tasks:
                task.cancel()
            if client_tasks:
                await asyncio.gather(*client_tasks, return_exceptions=True)

    def stop(self, timeout_sec: float = WORKER_SHUTDOWN_TIMEOUT_S) -> None:
        deadline = time.monotonic() + timeout_sec
        with self._lock:
            self._stop_requested.set()
            loop = self._loop
            sht = self._shutdown_event
            if loop and loop.is_running() and sht:
                loop.call_soon_threadsafe(sht.set)
            thread = self._thread
            if thread and thread.is_alive():
                thread.join(timeout=max(0, deadline - time.monotonic()))
            if thread and not thread.is_alive():
                self._thread = None
            self._loop = None
            self._server = None
            self._shutdown_event = None


# ==============================================================================
# 5. Orthogonal Atomic Capability: TelemetrySampler
# ==============================================================================

class TelemetrySampler:
    """One reusable sampling worker per name with synchronous termination."""

    def __init__(self):
        self._watches: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    def start_watch(
        self,
        name: str,
        interval_ms: int,
        duration_ms: int,
        sample_fn: Callable[[], int],
        on_sample: Callable[[str, int], None],
    ) -> int:
        if interval_ms <= 0 or duration_ms <= 0:
            raise ValueError("Telemetry interval and duration must be positive")
        clamped_interval = max(TELEMETRY_MIN_INTERVAL_MS, interval_ms)

        while True:
            with self._lock:
                entry = self._watches.get(name)

            if entry is not None:
                # The configuration lock also serializes emissions. Updating it
                # therefore drains any old push before the refresh is acknowledged.
                with entry["lock"]:
                    if entry["stop"].is_set():
                        if entry["thread"].is_alive():
                            raise RuntimeError(f"Previous telemetry watch '{name}' still stopping")
                    elif entry["thread"].is_alive() and not entry["retiring"]:
                        entry["generation"] += 1
                        entry["interval_ms"] = clamped_interval
                        entry["duration_ms"] = duration_ms
                        entry["deadline"] = time.monotonic() + duration_ms / 1000.0
                        entry["sample_fn"] = sample_fn
                        entry["on_sample"] = on_sample
                        entry["wake"].set()
                        return clamped_interval

            stop_evt = threading.Event()
            wake_evt = threading.Event()
            new_entry = {
                "thread": None,
                "stop": stop_evt,
                "wake": wake_evt,
                "lock": threading.Lock(),
                "generation": 1,
                "retiring": False,
                "interval_ms": clamped_interval,
                "duration_ms": duration_ms,
                "deadline": time.monotonic() + duration_ms / 1000.0,
                "sample_fn": sample_fn,
                "on_sample": on_sample,
            }
            t = threading.Thread(
                target=self._worker,
                args=(name, new_entry),
                daemon=True,
                name=f"mycro-telemetry-{name}",
            )
            new_entry["thread"] = t

            with self._lock:
                current = self._watches.get(name)
                if current is not None and current["thread"].is_alive():
                    if current["stop"].is_set():
                        raise RuntimeError(f"Previous telemetry watch '{name}' still stopping")
                    if not current["retiring"]:
                        # Another caller installed a live generation while this
                        # entry was being prepared; retry against that generation.
                        continue
                self._watches[name] = new_entry
                try:
                    t.start()
                except Exception:
                    if self._watches.get(name) is new_entry:
                        self._watches.pop(name, None)
                    raise
            return clamped_interval

    def stop_watch(self, name: str, timeout_sec: float = UNWATCH_STOP_TIMEOUT_S) -> bool:
        """Blocks until the named worker terminates, or returns False on timeout."""
        with self._lock:
            entry = self._watches.get(name)
        if entry is None:
            return True

        entry["stop"].set()
        entry["wake"].set()
        thread = entry["thread"]
        deadline = time.monotonic() + timeout_sec
        thread.join(timeout=max(0, deadline - time.monotonic()))
        if thread.is_alive():
            # Keep terminal workers registered so later stop calls and shutdown
            # can continue joining them instead of losing a live producer.
            return False
        with self._lock:
            if self._watches.get(name) is entry:
                self._watches.pop(name, None)
        return True

    def stop_all(self, timeout_sec: float = WORKER_SHUTDOWN_TIMEOUT_S) -> bool:
        with self._lock:
            entries = list(self._watches.values())
        for entry in entries:
            entry["stop"].set()
            entry["wake"].set()

        deadline = time.monotonic() + timeout_sec
        all_stopped = True
        for entry in entries:
            thread = entry["thread"]
            thread.join(timeout=max(0, deadline - time.monotonic()))
            if thread.is_alive():
                all_stopped = False
        return all_stopped

    def _worker(self, name: str, entry: Dict[str, Any]) -> None:
        stop_evt = entry["stop"]
        wake_evt = entry["wake"]
        cfg_lock = entry["lock"]
        try:
            while not stop_evt.is_set():
                with cfg_lock:
                    # Clear under the configuration lock so an update cannot be
                    # lost between choosing a sleep and consuming its wakeup.
                    wake_evt.clear()
                    if stop_evt.is_set():
                        break
                    generation = entry["generation"]
                    interval_ms = entry["interval_ms"]
                    deadline = entry["deadline"]
                    sample_fn = entry["sample_fn"]
                    on_sample = entry["on_sample"]
                    if time.monotonic() >= deadline:
                        entry["retiring"] = True
                        break

                try:
                    val = sample_fn()
                    with cfg_lock:
                        if stop_evt.is_set() or generation != entry["generation"]:
                            continue
                        if time.monotonic() >= deadline:
                            entry["retiring"] = True
                            break
                        # Refresh takes this same lock; unwatch joins the worker.
                        # Neither can be acknowledged in the middle of an old push.
                        on_sample(name, val)
                except Exception as e:
                    with cfg_lock:
                        stale = stop_evt.is_set() or generation != entry["generation"]
                        if not stale:
                            entry["retiring"] = True
                    if stale:
                        continue
                    if time.monotonic() < deadline:
                        log_msg(LOG_WARNING, f"mycro: telemetry sample error [{name}]: {e}")
                    break

                with cfg_lock:
                    if stop_evt.is_set():
                        break
                    if generation != entry["generation"]:
                        continue
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        entry["retiring"] = True
                        break
                    wait_s = min(interval_ms / 1000.0, remaining)
                wake_evt.wait(wait_s)
        finally:
            with cfg_lock:
                entry["retiring"] = True
            with self._lock:
                if self._watches.get(name) is entry:
                    self._watches.pop(name, None)


# ==============================================================================
# 6. Orthogonal Atomic Capability: WireSession
# ==============================================================================

class WireSession:
    """Protocol v1 TCP client (:7373) maintaining little-endian framed messages."""

    def __init__(self, host: str, port: int = DEFAULT_RAW_PORT, token: str = ""):
        self.host = host
        self.port = port
        self.token = token
        self.sock: Optional[socket.socket] = None
        self._stream_counter = 0
        self._write_lock = threading.Lock()
        self._closed = False

        self._action_handler: Optional[Callable[[dict], None]] = None
        self._connect_handler: Optional[Callable[[], None]] = None

    def set_action_handler(self, handler: Callable[[dict], None]) -> None:
        self._action_handler = handler

    def set_connect_handler(self, handler: Callable[[], None]) -> None:
        self._connect_handler = handler

    def next_stream_id(self) -> int:
        self._stream_counter = (self._stream_counter + 1) & 0xFFFF
        if self._stream_counter == 0:
            self._stream_counter = 1
        return self._stream_counter

    def connect(self, timeout: float = WIRE_CONNECT_TIMEOUT_S) -> None:
        """Establishes TCP connection and leads with 37-byte Auth Prologue."""
        self.disconnect()
        self._closed = False

        target_host = self.host
        # Pre-resolve IPv4 specifically to eliminate macOS mDNS AAAA (IPv6) 5s timeouts
        try:
            infos = socket.getaddrinfo(self.host, self.port, socket.AF_INET, socket.SOCK_STREAM)
            if infos:
                target_host = infos[0][4][0]
        except Exception:
            pass

        s = socket.create_connection((target_host, self.port), timeout=timeout)
        # Reads treat this as a poll quantum; writes must never block forever
        # while holding the lock needed by unwatch and protocol error replies.
        s.settimeout(WIRE_IO_TIMEOUT_S)

        # 37-byte Auth Prologue: AUTH_MAGIC(4) + PROTO_VERSION(1) + SHA256(32)
        digest = hashlib.sha256(self.token.encode("utf-8")).digest()
        prologue = AUTH_MAGIC + bytes([PROTO_VERSION]) + digest
        s.sendall(prologue)
        self.sock = s

        if self._connect_handler:
            self._connect_handler()

    def disconnect(self) -> None:
        """Safely shuts down the socket without blocking on active write lock."""
        self._closed = True
        s = self.sock
        self.sock = None
        if s:
            try:
                s.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            try:
                s.close()
            except Exception:
                pass

    def _write_parts(self, parts, deadline: Optional[float]) -> None:
        if deadline is None:
            deadline = time.monotonic() + WIRE_IO_TIMEOUT_S
        acquired = self._write_lock.acquire(timeout=_remaining(deadline))
        if not acquired:
            self.disconnect()
            raise TimeoutError("Wire write deadline expired waiting for another writer")
        try:
            if not self.sock or self._closed:
                raise ConnectionError("WireSession is disconnected")
            for part in parts:
                self.sock.settimeout(_remaining(deadline))
                self.sock.sendall(part)
        except Exception:
            # A timeout after a partial write desynchronizes framing.
            self.disconnect()
            raise
        finally:
            self._write_lock.release()

    def send_envelope(self, env: dict, deadline: Optional[float] = None) -> None:
        """Sends a kind-0 JSON envelope enforcing little-endian <I header and metadata."""
        env = dict(env)
        env["v"] = PROTO_VERSION
        if "ts" not in env:
            env["ts"] = int(time.time() * 1000)

        payload = json.dumps(env, separators=(",", ":")).encode("utf-8")
        header = struct.pack("<I", len(payload))
        self._write_parts((header + payload,), deadline)

    def send_chunks(self, stream_id: int, data: bytes, deadline: Optional[float] = None) -> None:
        """Streams kind-1 binary chunks with little-endian <I and strictly flags=0."""
        total_len = len(data)
        num_chunks = (total_len + MAX_CHUNK_PAYLOAD - 1) // MAX_CHUNK_PAYLOAD if total_len > 0 else 1

        def parts():
            for idx in range(num_chunks):
                start = idx * MAX_CHUNK_PAYLOAD
                chunk_slice = data[start:start + MAX_CHUNK_PAYLOAD]
                prefix = struct.pack("<HH", stream_id, 0)  # flags MUST strictly be 0
                chunk_payload = prefix + chunk_slice
                header = struct.pack("<I", 0x80000000 | len(chunk_payload))
                yield header + chunk_payload

        self._write_parts(parts(), deadline)

    def recv_loop(self, stop_evt: threading.Event) -> None:
        """Main read loop dispatching incoming envelopes to the Action Router."""
        while not stop_evt.is_set() and not self._closed:
            s = self.sock
            if not s:
                break
            try:
                hdr_bytes = self._recv_exact(s, 4, stop_evt)
                (h,) = struct.unpack("<I", hdr_bytes)
                kind = h >> 31
                length = h & 0x7FFFFFFF
                if length > MAX_FRAME_PAYLOAD:
                    raise ConnectionError(f"Oversize TCP frame length: {length} bytes")
                payload = self._recv_exact(s, length, stop_evt)

                if kind == 0:
                    env = json.loads(payload.decode("utf-8"))
                    if self._action_handler:
                        self._action_handler(env)
            except Exception:
                break
        self.disconnect()

    @staticmethod
    def _recv_exact(sock: socket.socket, n: int, stop_evt: threading.Event) -> bytes:
        buf = bytearray()
        while len(buf) < n:
            if stop_evt.is_set():
                raise ConnectionError("Receive stopped")
            try:
                chunk = sock.recv(n - len(buf))
            except socket.timeout:
                continue
            if not chunk:
                raise ConnectionError("Peer disconnected")
            buf.extend(chunk)
        return bytes(buf)


# ==============================================================================
# 7. Declarative Action Router (ProtoActionRouter)
# ==============================================================================

class ProtoActionRouter:
    """Routes wire requests by orchestrating orthogonal atomic capabilities."""

    def __init__(
        self,
        capture: Optional[CaptureEngine],
        preview: Optional[EphemeralHttpServer],
        sampler: TelemetrySampler,
        wire: WireSession,
    ):
        self.capture = capture
        self.preview = preview
        self.sampler = sampler
        self.wire = wire
        self._routes: Dict[str, Callable[[int, dict], None]] = {
            "img.frame": self._handle_frame,
            "img.arm_preview": self._handle_preview,
            "img.watch": self._handle_watch,
            "img.unwatch": self._handle_unwatch,
        }

        self.wire.set_action_handler(self.dispatch_action)
        self.wire.set_connect_handler(self.on_connected)

    def action(self, verb: str):
        """Declarative route decorator modeled after the devicetest registry."""
        def decorator(fn: Callable[[int, dict], None]):
            self._routes[verb] = fn
            return fn
        return decorator

    def on_connected(self) -> None:
        """Announces capture provider presence upon link establishment."""
        self.wire.send_envelope({
            "type": "img.hello",
            "id": 0,
            "payload": {"caps": ["frame", "direct_preview"]},
        })

    def _send_error(self, rid: int, code: str, message: str, deadline: Optional[float] = None) -> None:
        """Sends standardized protocol error envelope if request id is non-zero."""
        if rid != 0:
            self.wire.send_envelope({
                "type": "error",
                "id": rid,
                "payload": {"code": code, "message": message},
            }, deadline=deadline)

    def dispatch_action(self, env: dict) -> None:
        verb = env.get("type", "")
        rid = env.get("id", 0)
        payload = env.get("payload", {})

        # Error reflection defense: drop incoming error or ok terminal envelopes
        if verb == "error" or verb.endswith(".ok"):
            return

        handler = self._routes.get(verb)
        if handler:
            request_deadline = time.monotonic() + DEVICE_EXT_TIMEOUT_MS / 1000.0
            try:
                handler(rid, payload, request_deadline)
            except Exception as e:
                log_msg(LOG_WARNING, f"mycro: action '{verb}' failed: {e}")
                self._send_error(rid, "capture_failed", str(e), deadline=request_deadline)
        else:
            self._send_error(rid, "unknown_type", f"Unhandled verb: {verb}")

    def _send_stream_reply(
        self,
        rid: int,
        data: bytes,
        native_w: int,
        native_h: int,
        w: int,
        h: int,
        fmt: str,
        deadline: float,
    ) -> None:
        sid = self.wire.next_stream_id()
        chunks = (len(data) + MAX_CHUNK_PAYLOAD - 1) // MAX_CHUNK_PAYLOAD if data else 1
        self.wire.send_envelope({
            "type": "img.frame.ok",
            "id": rid,
            "payload": {
                "width": native_w,
                "height": native_h,
                "w": w,
                "h": h,
                "format": fmt,
                "stream_id": sid,
                "bytes": len(data),
                "chunks": chunks,
            },
        }, deadline=deadline)
        self.wire.send_chunks(sid, data, deadline=deadline)

    def _handle_frame(self, rid: int, p: dict, deadline: float) -> None:
        if self.capture is None:
            self._send_error(rid, "capture_failed", "Capture engine is not available")
            return

        # 1. Fail-fast parameter validation before capture
        has_points = "points" in p
        has_rect = "rect" in p

        if has_points and has_rect:
            self._send_error(rid, "field", "Cannot specify both 'points' and 'rect'")
            return
        if not has_points and not has_rect:
            self._send_error(rid, "field", "Must specify either 'points' or 'rect'")
            return

        if has_points:
            pts = p["points"]
            if not isinstance(pts, (list, tuple)) or len(pts) > MAX_POINTS:
                self._send_error(rid, "field", f"Points count exceeds {MAX_POINTS} or invalid list")
                return
            for pt in pts:
                if not isinstance(pt, (list, tuple)) or len(pt) != 2 or not isinstance(pt[0], int) or not isinstance(pt[1], int):
                    self._send_error(rid, "field", f"Invalid point {pt}: must be (int, int)")
                    return
            bmp = self.capture.get_raw_bmp(deadline=deadline - WIRE_IO_TIMEOUT_S)
            _, src_w, src_h, _ = self.capture.parse_bmp_header(bmp)
            colors = self.capture.extract_points(bmp, pts)
            self.wire.send_envelope({
                "type": "img.frame.ok",
                "id": rid,
                "payload": {"width": src_w, "height": abs(src_h), "colors": colors},
            }, deadline=deadline)
        elif has_rect:
            r = p["rect"]
            if not isinstance(r, dict):
                self._send_error(rid, "field", "Rect must be a dictionary")
                return
            for k in ("x", "y", "w", "h"):
                if k not in r or not isinstance(r[k], int):
                    self._send_error(rid, "field", f"Rect field '{k}' missing or non-integer")
                    return
            rw, rh = r["w"], r["h"]
            if rw <= 0 or rh <= 0 or rw > MAX_RECT_EDGE or rh > MAX_RECT_EDGE:
                self._send_error(rid, "field", f"Rect edge exceeds {MAX_RECT_EDGE}")
                return
            bmp = self.capture.get_raw_bmp(deadline=deadline - WIRE_IO_TIMEOUT_S)
            _, src_w, src_h, _ = self.capture.parse_bmp_header(bmp)
            raw_rgb = self.capture.extract_rect(bmp, r)
            self._send_stream_reply(rid, raw_rgb, src_w, abs(src_h), rw, rh, "rgb8", deadline)

    def _handle_preview(self, rid: int, p: dict, deadline: float) -> None:
        if self.capture is None or self.preview is None:
            self._send_error(rid, "capture_failed", "Capture or preview engine is not available")
            return
        nonce = p.get("nonce", "")
        w, h = self.capture.get_dimensions(deadline=deadline - WIRE_IO_TIMEOUT_S)
        port = self.preview.serve(
            token=self.wire.token,
            nonce=nonce,
            data_producer=self.capture.get_raw_png,
            ttl_sec=PREVIEW_LEASE_TTL_S,
            start_deadline=deadline - WIRE_IO_TIMEOUT_S,
        )
        self.wire.send_envelope({
            "type": "img.arm_preview.ok",
            "id": rid,
            "payload": {"port": port, "width": w, "height": h},
        }, deadline=deadline)

    def _handle_watch(self, rid: int, p: dict, deadline: float) -> None:
        if self.capture is None:
            self._send_error(rid, "capture_failed", "Capture engine is not available")
            return
        if "interval_ms" not in p or "duration_ms" not in p:
            self._send_error(rid, "field", "img.watch requires interval_ms and duration_ms")
            return
        name = p["name"]
        x, y = p["x"], p["y"]

        def do_sample() -> int:
            sample_deadline = time.monotonic() + UNWATCH_STOP_TIMEOUT_S
            bmp = self.capture.get_raw_bmp(deadline=sample_deadline)
            return self.capture.extract_points(bmp, [(x, y)])[0]

        def on_sample(s_name: str, val: int):
            self.wire.send_envelope({
                "type": "event.push",
                "id": 0,
                "payload": {"name": f"img.{s_name}", "fields": {"val": val}},
            })

        eff_interval = self.sampler.start_watch(
            name=name,
            interval_ms=p["interval_ms"],
            duration_ms=p["duration_ms"],
            sample_fn=do_sample,
            on_sample=on_sample,
        )
        self.wire.send_envelope({
            "type": "img.watch.ok",
            "id": rid,
            "payload": {"interval_ms": eff_interval},
        }, deadline=deadline)

    def _handle_unwatch(self, rid: int, p: dict, deadline: float) -> None:
        name = p.get("name", "")
        stop_timeout = max(0, deadline - WIRE_IO_TIMEOUT_S - time.monotonic())
        stopped = self.sampler.stop_watch(name, timeout_sec=stop_timeout)
        if stopped:
            self.wire.send_envelope({
                "type": "img.unwatch.ok",
                "id": rid,
                "payload": {},
            }, deadline=deadline)
        else:
            # Wire writes are bounded. Report the single-watch failure while
            # preserving healthy subscriptions on the same connection; an
            # actual write failure still disconnects the desynchronized stream.
            log_msg(LOG_WARNING, f"mycro: watch '{name}' failed to stop within deadline")
            self._send_error(rid, "timeout", f"Watch '{name}' failed to stop within deadline", deadline=deadline)


# ==============================================================================
# 8. Background Lifecycle & Reconnection Worker
#
# OBS UI/UX LIFECYCLE INVARIANTS (Hard-Earned Host Architecture Rules):
# 1. Keystroke Debounce & Idle Mode: OBS calls script_update() on EVERY keystroke.
#    Never attempt DNS or socket I/O while idle (is_active=False). Incomplete hosts
#    (e.g., 'mycro.l') throw Errno 8 and flood Script Log if unshielded.
# 2. Link-Drop Discipline: If link drops and auto_connect is False, revert to idle
#    immediately (is_active=False). Never run an unthrottled reconnect loop.
# 3. Decoupled Mutation: Changing capture source must NOT reset device TCP wire.
#    Only net_changed (host/port/token) triggers wire reconnect.
# 4. Synchronous UI Latch: connect_manual() waits up to 1.2s on _connected_event
#    so the button callback returns True with 'Disconnect' and 'Connected' in 1 click.
# 5. Read-Only Status Widget: OBS lacks native labels. Use disabled OBS_TEXT_INFO
#    with word wrap; route errors to the widget instead of spamming script_log.
# 6. Source Boundary Sentinel: Validate capture dimensions (w, h > 0) and warn in
#    status bar with OBS_TEXT_INFO_WARNING if the source is 0x0 or invalid.
# ==============================================================================

class ProviderWorker:
    """Manages background WebSocket and WireSession connection loops."""

    def __init__(self):
        self.device_host = DEFAULT_DEVICE_HOST
        self.device_port = DEFAULT_RAW_PORT
        self.device_token = ""
        self.source_name = ""
        self.auto_connect = False
        self.is_active = False
        self.status_text = "Disconnected (idle)"

        self.ws_client = ObsWsClient()
        self.capture = CaptureEngine(self.ws_client)
        self.preview = EphemeralHttpServer()
        self.sampler = TelemetrySampler()
        self.wire: Optional[WireSession] = None
        self.router: Optional[ProtoActionRouter] = None

        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._connected_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._shutdown_deadline: Optional[float] = None

    def is_connected(self) -> bool:
        return bool(self.wire and self.wire.sock and not self.wire._closed)

    def get_status_summary(self) -> str:
        if not self.is_active:
            return "Disconnected (idle)"
        if not self.is_connected():
            return self.status_text
        host = self.device_host or DEFAULT_DEVICE_HOST
        base = f"Connected to {host}:{self.device_port}"

        target = self.source_name
        is_auto = False
        if not target:
            target = _cached_scene_name
            is_auto = True

        if not target:
            return f"{base} (Auto: waiting for active scene)"

        if obs is not None:
            src = obs.obs_get_source_by_name(target)
            if src is None:
                return f"{base} [WARNING: Source '{target}' not found!]"
            try:
                w = obs.obs_source_get_width(src) if hasattr(obs, "obs_source_get_width") else 1
                h = obs.obs_source_get_height(src) if hasattr(obs, "obs_source_get_height") else 1
                if w <= 0 or h <= 0:
                    return f"{base} [WARNING: Source '{target}' has 0x0 size!]"
            finally:
                obs.obs_source_release(src)
            label = f"'{target}' [Auto]" if is_auto else f"'{target}'"
            return f"{base} (Source: {label} {w}x{h})"
        return f"{base} (Source: '{target}')"

    def connect_manual(self, wait_timeout: float = UI_CONNECT_LATCH_S) -> bool:
        host = self.device_host or DEFAULT_DEVICE_HOST
        self.device_host = host
        if self.is_connected():
            self.status_text = f"Connected to {host}:{self.device_port}"
            log_msg(LOG_INFO, f"mycro: already connected to {host}:{self.device_port}")
            _update_status_property()
            return True
        self._connected_event.clear()
        self.status_text = f"Connecting to {host}:{self.device_port}..."
        log_msg(LOG_INFO, "mycro: connect requested")
        self.is_active = True
        self._wake_event.set()
        if wait_timeout > 0:
            self._connected_event.wait(wait_timeout)
        _update_status_property()
        return self.is_connected()

    def disconnect_manual(self) -> None:
        self.status_text = "Disconnected (idle)"
        log_msg(LOG_INFO, "mycro: disconnect requested")
        self.is_active = False
        self._connected_event.clear()
        if self.wire:
            self.wire.disconnect()
        self.ws_client.disconnect()
        self._wake_event.set()
        _update_status_property()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._wake_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="mycro-provider-worker")
        self._thread.start()

    def stop(self) -> None:
        deadline = self._shutdown_deadline
        if deadline is None:
            deadline = time.monotonic() + WORKER_SHUTDOWN_TIMEOUT_S
            self._shutdown_deadline = deadline
        self._stop_event.set()
        self._wake_event.set()
        self._connected_event.clear()
        try:
            if self.wire:
                self.wire.disconnect()
            self.ws_client.disconnect()
            if self._thread:
                self._thread.join(timeout=max(0, deadline - time.monotonic()))
                if not self._thread.is_alive():
                    self._thread = None
            self.sampler.stop_all(timeout_sec=max(0, deadline - time.monotonic()))
            self.preview.stop(timeout_sec=max(0, deadline - time.monotonic()))
        finally:
            if self._thread is None or not self._thread.is_alive():
                self._shutdown_deadline = None

    def update_config(self, host: str, port: int, token: str, source: str, auto_connect: bool) -> None:
        net_changed = (
            host != self.device_host
            or port != self.device_port
            or token != self.device_token
        )
        src_changed = source != self.source_name
        self.device_host = host
        self.device_port = port
        self.device_token = token
        self.source_name = source
        self.auto_connect = auto_connect
        if src_changed:
            self.capture.update_source(source)
        # If network settings changed while actively connected, disconnect cleanly to idle
        # NEVER trigger reconnection or DNS lookup while user is typing in property fields!
        if net_changed and self.is_active:
            self.disconnect_manual()
            self.status_text = "Disconnected (configuration changed)"
            _update_status_property()

    def _run(self) -> None:
        while not self._stop_event.is_set():
            if not self.is_active:
                self._connected_event.clear()
                self._wake_event.wait()
                self._wake_event.clear()
                continue

            if not self.device_host:
                self.status_text = "Error: No device host configured"
                _update_status_property()
                self._wake_event.wait()
                self._wake_event.clear()
                continue

            # Check if local OBS WebSocket server is enabled
            ws_enabled, ws_port, ws_pwd = ObsWsConfig.load()
            if not ws_enabled:
                self.status_text = "Error: OBS WebSocket is disabled (Tools -> WebSocket Server Settings)"
                log_msg(LOG_WARNING, "mycro: OBS WebSocket server is disabled in OBS settings")
                self.is_active = False
                _update_status_property()
                continue

            self.status_text = f"Connecting to {self.device_host}:{self.device_port}..."
            _update_status_property()

            try:
                # 1. Connect OBS WebSocket
                self.ws_client.port = ws_port
                self.ws_client.password = ws_pwd
                self.ws_client.connect(timeout=RPC_HANDSHAKE_TIMEOUT_S)

                # 2. Connect WireSession to MYCRO device
                wire = WireSession(self.device_host, self.device_port, self.device_token)
                self.wire = wire
                self.router = ProtoActionRouter(self.capture, self.preview, self.sampler, wire)

                log_msg(LOG_INFO, f"mycro: connecting to {self.device_host}:{self.device_port}...")
                wire.connect(timeout=WIRE_CONNECT_TIMEOUT_S)
                self._connected_event.set()
                self.status_text = f"Connected to {self.device_host}:{self.device_port}"
                log_msg(LOG_INFO, f"mycro: connected to {self.device_host}:{self.device_port}")
                _update_status_property()
                wire.recv_loop(self._stop_event)
            except Exception as e:
                self._connected_event.clear()
                self.status_text = f"Connection error: {e}"
                log_msg(LOG_WARNING, f"mycro: connection failed: {e}")
                _update_status_property()
            finally:
                self._connected_event.clear()
                cleanup_deadline = self._shutdown_deadline
                if cleanup_deadline is None:
                    cleanup_deadline = time.monotonic() + WORKER_SHUTDOWN_TIMEOUT_S
                # Break blocked capture and socket sends before joining samplers.
                if self.wire:
                    self.wire.disconnect()
                self.ws_client.disconnect()
                self.sampler.stop_all(timeout_sec=max(0, cleanup_deadline - time.monotonic()))
                self.preview.stop(timeout_sec=max(0, cleanup_deadline - time.monotonic()))
                if self._shutdown_deadline is cleanup_deadline:
                    self._shutdown_deadline = None

            # Determine reconnection behavior based on auto_connect
            if self.is_active and not self._stop_event.is_set():
                if self.auto_connect:
                    self.status_text = f"Reconnecting in {int(WORKER_RECONNECT_INTERVAL_S)}s..."
                    _update_status_property()
                    self._wake_event.wait(timeout=WORKER_RECONNECT_INTERVAL_S)
                    self._wake_event.clear()
                else:
                    self.is_active = False
                    if not self.status_text.startswith("Connection error") and not self.status_text.startswith("Error:"):
                        self.status_text = "Disconnected (idle)"
                    _update_status_property()


# ==============================================================================
# 9. OBS Python Frontend Lifecycle & UI Bindings
# ==============================================================================

_worker = ProviderWorker()
_current_settings = None


def _update_status_property() -> str:
    global _current_settings
    summary = _worker.get_status_summary()
    if obs is not None and _current_settings is not None:
        obs.obs_data_set_string(_current_settings, "status_info", summary)
    return summary


def on_toggle_connect_clicked(props, prop) -> bool:
    if _worker.is_connected():
        _worker.disconnect_manual()
    else:
        _worker.connect_manual()
    new_label = "Disconnect" if _worker.is_connected() else "Connect"
    if hasattr(obs, "obs_property_set_description"):
        obs.obs_property_set_description(prop, new_label)
    _update_status_property()
    return True


def on_refresh_clicked(props, prop) -> bool:
    global _cached_scene_name
    curr = get_current_scene_name()
    if curr:
        _cached_scene_name = curr
    summary = _update_status_property()
    log_msg(LOG_INFO, f"mycro: status refresh -> {summary}")
    btn = obs.obs_properties_get(props, "btn_toggle_connect")
    if btn and hasattr(obs, "obs_property_set_description"):
        obs.obs_property_set_description(btn, "Disconnect" if _worker.is_connected() else "Connect")
    return True


def script_description():
    return (
        "MYCRO capture provider: serves img.frame extraction requests from the device.\n"
        "Live connection and capture source status are displayed in Properties below."
    )


def script_defaults(settings):
    if obs is None:
        return
    obs.obs_data_set_default_string(settings, "host", DEFAULT_DEVICE_HOST)
    obs.obs_data_set_default_int(settings, "port", DEFAULT_RAW_PORT)
    obs.obs_data_set_default_bool(settings, "auto_connect", False)
    obs.obs_data_set_default_string(settings, "source", "")
    obs.obs_data_set_default_string(settings, "status_info", "Disconnected (idle)")


def script_update(settings):
    global _current_settings, _cached_scene_name
    if obs is None:
        return
    _current_settings = settings
    host = obs.obs_data_get_string(settings, "host") or DEFAULT_DEVICE_HOST
    port = obs.obs_data_get_int(settings, "port") or DEFAULT_RAW_PORT
    token = obs.obs_data_get_string(settings, "token")
    source = obs.obs_data_get_string(settings, "source")
    auto_connect = obs.obs_data_get_bool(settings, "auto_connect")
    curr = get_current_scene_name()
    if curr:
        _cached_scene_name = curr
    _worker.update_config(host, port, token, source, auto_connect)
    _update_status_property()


def script_properties():
    if obs is None:
        return None
    props = obs.obs_properties_create()

    # 1. Connection status text widget (disabled read-only, exact v1 placement at top)
    summary = _update_status_property()
    p_status = obs.obs_properties_add_text(
        props, "status_info", "Connection status",
        getattr(obs, "OBS_TEXT_INFO", obs.OBS_TEXT_DEFAULT)
    )
    if hasattr(obs, "obs_property_set_enabled"):
        obs.obs_property_set_enabled(p_status, False)
    if hasattr(obs, "obs_property_text_set_info_word_wrap"):
        obs.obs_property_text_set_info_word_wrap(p_status, True)
    if hasattr(obs, "obs_property_text_set_info_type"):
        if _worker.is_connected():
            itype = getattr(obs, "OBS_TEXT_INFO_WARNING", 2) if "WARNING" in summary else getattr(obs, "OBS_TEXT_INFO_NORMAL", 1)
        elif "error" in summary.lower() or "fail" in summary.lower() or "link down" in summary.lower():
            itype = getattr(obs, "OBS_TEXT_INFO_ERROR", 3)
        else:
            itype = getattr(obs, "OBS_TEXT_INFO_NORMAL", 1)
        obs.obs_property_text_set_info_type(p_status, itype)

    # 2. Capture source dropdown (Auto program scene default)
    lst = obs.obs_properties_add_list(
        props, "source", "Capture source",
        obs.OBS_COMBO_TYPE_LIST, obs.OBS_COMBO_FORMAT_STRING
    )
    obs.obs_property_list_add_string(lst, "--- Current Program Scene (Auto) ---", "")
    sources = obs.obs_enum_sources()
    if sources:
        video_flag = getattr(obs, "OBS_SOURCE_VIDEO", 1)
        for s in sources:
            flags = obs.obs_source_get_output_flags(s) if hasattr(obs, "obs_source_get_output_flags") else video_flag
            if flags & video_flag:
                name = obs.obs_source_get_name(s)
                obs.obs_property_list_add_string(lst, name, name)
        obs.source_list_release(sources)

    # 3. Connection network inputs (exact v1 copy)
    obs.obs_properties_add_text(props, "host", "Device Host", obs.OBS_TEXT_DEFAULT)
    obs.obs_properties_add_int(props, "port", "Device port", 1, 65535, 1)
    obs.obs_properties_add_text(props, "token", "Device token", obs.OBS_TEXT_PASSWORD)
    obs.obs_properties_add_bool(props, "auto_connect", "Auto-connect on OBS launch")

    # 4. Action buttons (exact v1 copy)
    btn_label = "Disconnect" if _worker.is_connected() else "Connect"
    obs.obs_properties_add_button(props, "btn_toggle_connect", btn_label, on_toggle_connect_clicked)
    obs.obs_properties_add_button(props, "refresh", "Refresh Status", on_refresh_clicked)
    return props


def on_frontend_event(event):
    global _cached_scene_name
    if obs is None:
        return
    scene_event = getattr(obs, "OBS_FRONTEND_EVENT_SCENE_CHANGED", 0)
    if event == scene_event:
        curr = get_current_scene_name()
        if curr:
            _cached_scene_name = curr
            _update_status_property()


def script_load(settings):
    global _current_settings
    _current_settings = settings
    if obs is not None and hasattr(obs, "obs_frontend_add_event_callback"):
        obs.obs_frontend_add_event_callback(on_frontend_event)
    _worker.start()
    script_update(settings)
    auto_connect = obs.obs_data_get_bool(settings, "auto_connect") if obs else False
    if auto_connect:
        log_msg(LOG_INFO, "mycro: auto-connecting on load")
        _worker.connect_manual(wait_timeout=0)
    else:
        log_msg(LOG_INFO, "mycro: loaded idle (manual connect required)")
        _worker.is_active = False


def script_unload():
    if obs is not None and hasattr(obs, "obs_frontend_remove_event_callback"):
        try:
            obs.obs_frontend_remove_event_callback(on_frontend_event)
        except Exception:
            pass
    _worker.stop()

