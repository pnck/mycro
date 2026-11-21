"""MYCRO Device Firmware Entry Point (Composition Root).

Assembles hardware providers, the macro engine, and host runtime services
(mDNS networking, HTTP server with Web UI/REST, raw TCP channel, and image provider).
"""

import gc
import os
import traceback
try:
    import asyncio
except ImportError:
    import _asyncio as asyncio

try:
    import wifi
    import socketpool
except ImportError:
    wifi = None
    socketpool = None

import proto
from hid_adafruit import AdafruitHIDProvider
from runtime import Runtime
from macro import Macro
from services.network import setup_network
from services.runner import MacroRunner
from services.raw import RawServer, get_raw_port
from services.img import ImgProvider
from services.http import HttpServer


# Environment configuration from settings.toml
WIFI_SSID = os.getenv("MYCRO_WIFI_SSID") or ""
WIFI_PASSWORD = os.getenv("MYCRO_WIFI_PASSWORD") or ""
TOKEN = os.getenv("MYCRO_TOKEN")
HOSTNAME = os.getenv("MYCRO_HOSTNAME") or "mycro"
RAW_PORT = get_raw_port()


# Core macro engine and hardware abstraction
hid = AdafruitHIDProvider()
runtime = Runtime()
runtime.register_ns("sys", {
    "free": ([], "int", lambda: gc.mem_free()),
})
macro = Macro(hid, runtime)


# Service components
pool = socketpool.SocketPool(wifi.radio) if (wifi and socketpool) else None
runner = MacroRunner(macro)
img_provider = ImgProvider(runtime)
img_provider.register_namespaces()
runner.set_img_provider(img_provider)

raw_server = RawServer(pool, runtime, runner, token=TOKEN, port=RAW_PORT)
runner.set_raw_server(raw_server)
img_provider.bind_raw_server(raw_server)

runtime.register_signal("net.msg")
runtime.register_ns("net", {
    "send": (["str"], "int", lambda tag: raw_server.broadcast(proto.push("net.msg", {"tag": tag}))),
})

http_server = HttpServer(pool, runner, img_provider=img_provider, token=TOKEN, hostname=HOSTNAME)


async def _http_loop():
    """Drives non-blocking HTTP polling."""
    while True:
        try:
            http_server.poll()
        except Exception as e:
            print("http handler error:", e)
            traceback.print_exception(e)
        await asyncio.sleep(0)


async def _main():
    net_info = await setup_network(WIFI_SSID, WIFI_PASSWORD, HOSTNAME)

    if net_info["online"]:
        ip = net_info["ip"]
        http_server.start(ip, 80)
        if not TOKEN:
            print("WARNING: MYCRO_TOKEN not set in settings.toml — auth disabled (dev mode)")
        await asyncio.gather(_http_loop(), raw_server.loop(), img_provider.lease_loop())
    else:
        # Offline mode: keep USB/serial/HID functional without periodic wakes.
        print("Edit settings.toml via CIRCUITPY drive, then reset to retry network services.")
        await asyncio.Event().wait()


if __name__ == "__main__" or True:
    asyncio.run(_main())
