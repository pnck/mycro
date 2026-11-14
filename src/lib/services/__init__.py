"""MYCRO Device Host Services Package.

Contains network connectivity, mDNS broadcasting, HTTP REST/WebUI daemons,
raw TCP companion channels, and image capture providers.
"""

from services.network import normalize_hostname, init_mdns, wifi_connect, setup_network
from services.runner import MacroRunner
from services.raw import RawServer, get_raw_port
from services.img import ImgProvider
from services.http import HttpServer, urldecode

__all__ = [
    "normalize_hostname",
    "init_mdns",
    "wifi_connect",
    "setup_network",
    "MacroRunner",
    "RawServer",
    "get_raw_port",
    "ImgProvider",
    "HttpServer",
    "urldecode",
]
