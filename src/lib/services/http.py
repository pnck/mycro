"""MYCRO HTTP Web Server & REST API Service.

Delivers gzipped Web UI from RAM with ETag / 304 caching, enforces Bearer auth,
handles mDNS 302 IP redirection with ?ip=1 failsafe bypass, and serves macro REST APIs.
"""

import hashlib
import json
try:
    import asyncio
except ImportError:
    import _asyncio as asyncio

try:
    from adafruit_httpserver import Server, Request, Response, FileResponse
except ImportError:
    Server = None
    Request = None
    Response = None
    FileResponse = None

from services.network import normalize_hostname


def urldecode(s):
    """Decode URL-encoded string.

    NOTE: adafruit_httpserver's form_data does NOT URL-decode values (verified
    against the pinned library version), so we decode here.
    """
    result = []
    i = 0
    while i < len(s):
        if s[i] == "%" and i + 2 < len(s):
            try:
                result.append(chr(int(s[i + 1 : i + 3], 16)))
                i += 3
                continue
            except Exception:
                pass
        elif s[i] == "+":
            result.append(" ")
            i += 1
            continue
        result.append(s[i])
        i += 1
    return "".join(result)


def _macro_form(request):
    """Extract and decode macro form fields. Returns (code, hold_ms, err)."""
    code = request.form_data.get("code", "")
    hold = request.form_data.get("hold_ms", "")
    if not code:
        return None, None, "No macro code"
    hold_ms = None
    if hold:
        try:
            val = int(hold)
            if not (0 <= val <= 65535):
                return urldecode(code), None, "Invalid hold_ms: must be in [0, 65535]"
            hold_ms = val
        except (ValueError, TypeError):
            return urldecode(code), None, f"Invalid hold_ms: {hold!r}"
    return urldecode(code), hold_ms, None


class HttpServer:
    """HTTP Web Server wrapping adafruit_httpserver."""

    def __init__(self, pool, macro_runner, img_provider=None, token=None, hostname="mycro"):
        self.pool = pool
        self.macro_runner = macro_runner
        self.img_provider = img_provider
        self.token = token
        self.label = normalize_hostname(hostname)
        self.domain = f"{self.label}.local"

        self.server = Server(pool, debug=False) if (pool and Server) else None

        self._index_body = None
        self._index_etag = None
        self._index_is_gz = False

        self._init_web_ui()
        if self.server:
            self._register_routes()

    def _init_web_ui(self):
        for path, is_gz in (("/index.html.gz", True), ("/index.html", False)):
            try:
                with open(path, "rb") as f:
                    self._index_body = f.read()
                self._index_is_gz = is_gz
                digest = hashlib.new("sha256", self._index_body).digest()
                self._index_etag = '"' + "".join(f"{b:02x}" for b in digest[:8]) + '"'
                print(f"Web UI: loaded {path} ({len(self._index_body)}B, etag {self._index_etag})")
                return
            except OSError:
                continue
        print("Web UI: neither /index.html.gz nor /index.html found")

    def _authorized(self, request):
        if not self.token:
            return True
        return request.headers.get("Authorization") == "Bearer " + self.token

    def _unauth(self, request):
        return Response(request, "Unauthorized", status=(401, "Unauthorized"), content_type="text/plain")

    def _should_redirect(self, host_header, query_params):
        """Check if request arrived via IP or alternate host and needs 302 redirect."""
        if not host_header:
            return False
        # If user explicitly requested IP bypass: ?ip=1
        if query_params and query_params.get("ip") == "1":
            return False

        host_name = host_header.split(":")[0].strip().lower()
        # If host already matches target mDNS domain, no redirect needed
        if host_name == self.domain:
            return False

        # Any raw IP or mismatch redirects to http://<label>.local/
        return True

    def _register_routes(self):
        server = self.server

        @server.route("/")
        def index(request: Request):
            host = request.headers.get("host", "")
            query = request.query_params or {}

            # IP 302 Redirection with ?ip=1 bypass
            if self._should_redirect(host, query):
                return Response(
                    request,
                    f"Redirecting to http://{self.domain}/\n",
                    status=(302, "Found"),
                    headers={"Location": f"http://{self.domain}/"},
                    content_type="text/plain",
                )

            if not self._index_body:
                return Response(
                    request, "<h1>index.html not found</h1>", status=(404, "Not Found"), content_type="text/html; charset=utf-8"
                )

            # 304 Not Modified negotiation via If-None-Match
            inm = request.headers.get("if-none-match")
            if inm and self._index_etag and self._index_etag in inm:
                return Response(request, status=(304, "Not Modified"))

            headers = {
                "Cache-Control": "public, max-age=86400, must-revalidate",
            }
            if self._index_etag:
                headers["ETag"] = self._index_etag
            if self._index_is_gz:
                headers["Content-Encoding"] = "gzip"

            return Response(request, self._index_body, headers=headers, content_type="text/html; charset=utf-8")

        @server.route("/favicon.ico")
        def favicon(request: Request):
            return Response(request, status=(204, "No Content"), content_type="image/x-icon")

        @server.route("/obs/obs_mycro.py")
        def download_obs_script(request: Request):
            try:
                return FileResponse(
                    request,
                    filename="obs_mycro.py",
                    root_path="/obs",
                    as_attachment=True,
                    download_filename="obs_mycro.py",
                )
            except OSError:
                return Response(
                    request,
                    "obs_mycro.py not found on device",
                    status=(404, "Not Found"),
                    content_type="text/plain; charset=utf-8",
                )

        @server.route("/macro", methods=["POST"])
        def run_macro(request: Request):
            if not self._authorized(request):
                return self._unauth(request)
            if self.macro_runner.is_running():
                return Response(request, "Busy: Macro is running", status=(409, "Conflict"), content_type="text/plain")

            code, hold_ms, form_err = _macro_form(request)
            if code is None:
                return Response(request, "Error: No macro code", content_type="text/plain")
            if form_err:
                return Response(request, f"Error: {form_err}", status=(400, "Bad Request"), content_type="text/plain; charset=utf-8")

            size, err = self.macro_runner.submit(code, hold_ms=hold_ms, source="http")
            if err:
                status_code = 400 if "Compile Error" in err else 500
                return Response(request, err, status=(status_code, "Bad Request" if status_code == 400 else "Server Error"), content_type="text/plain; charset=utf-8")

            return Response(request, f"Started: {size}B", content_type="text/plain; charset=utf-8")

        @server.route("/macro/status")
        def macro_status(request: Request):
            if not self._authorized(request):
                return self._unauth(request)
            body = self.macro_runner.get_status_string()
            return Response(request, body, content_type="text/plain; charset=utf-8")

        @server.route("/macro/abort", methods=["POST"])
        def macro_abort(request: Request):
            if not self._authorized(request):
                return self._unauth(request)
            aborted = self.macro_runner.abort()
            if aborted:
                return Response(request, "Abort requested", content_type="text/plain")
            return Response(request, "No macro running (released all keys)", content_type="text/plain")

        @server.route("/compile", methods=["POST"])
        def compile_macro(request: Request):
            if not self._authorized(request):
                return self._unauth(request)
            if self.macro_runner.is_running():
                return Response(request, "Busy: Macro is running", status=(409, "Conflict"), content_type="text/plain")

            code, hold_ms, form_err = _macro_form(request)
            if code is None:
                return Response(request, "Error: No macro code", content_type="text/plain")
            if form_err:
                return Response(request, f"Error: {form_err}", status=(400, "Bad Request"), content_type="text/plain; charset=utf-8")

            try:
                bytecode, err = self.macro_runner.macro.compile(code, press_hold_ms=hold_ms)
            except Exception as e:
                return Response(request, f"Compile failed: {type(e).__name__}: {e}", content_type="text/plain; charset=utf-8")

            if err:
                return Response(request, f"Compile Error: {err}", content_type="text/plain; charset=utf-8")

            return Response(request, self.macro_runner.macro.disassemble(bytecode), content_type="text/plain; charset=utf-8")

        @server.route("/img/capture", methods=["POST"])
        def img_capture_start(request: Request):
            if not self._authorized(request):
                return self._unauth(request)
            if self.macro_runner.is_running():
                return Response(request, "Busy: Macro is running", status=(409, "Conflict"), content_type="text/plain")

            if not self.img_provider:
                return Response(request, "Vision service not configured", status=(503, "Service Unavailable"), content_type="text/plain")

            if self.img_provider.ui_preview["pending"]:
                return Response(request, "busy", status=(409, "Conflict"), content_type="text/plain")

            self.img_provider.ui_preview.update(pending=True, data=None, err=None)

            async def _task():
                try:
                    res = await self.img_provider.arm_direct_preview()
                    self.img_provider.ui_preview["data"] = res
                except Exception as e:
                    self.img_provider.ui_preview["err"] = str(e)
                finally:
                    self.img_provider.ui_preview["pending"] = False

            asyncio.create_task(_task())
            return Response(request, "started", content_type="text/plain")

        @server.route("/img/capture", methods=["GET"])
        def img_capture_poll(request: Request):
            if not self._authorized(request):
                return self._unauth(request)
            if not self.img_provider:
                return Response(request, "Vision service not configured", status=(503, "Service Unavailable"), content_type="text/plain")

            ui = self.img_provider.ui_preview
            if ui["pending"]:
                return Response(request, "", status=(204, "No Content"), content_type="text/plain")
            if ui["err"]:
                err = ui["err"]
                ui["err"] = None
                return Response(request, err, status=(502, "Bad Gateway"), content_type="text/plain")
            if ui["data"] is None:
                return Response(request, "none", status=(404, "Not Found"), content_type="text/plain")

            data = ui["data"]
            ui["data"] = None
            return Response(request, body=json.dumps(data), content_type="application/json")

    def start(self, ip, port=80):
        if self.server:
            self.server.start(ip, port)
            print(f"HTTP server at http://{ip}:{port} (Domain: http://{self.domain}/)")

    def poll(self):
        if self.server:
            self.server.poll()
