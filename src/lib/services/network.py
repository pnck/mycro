"""WiFi Connection, Power Management and RFC 6762 mDNS Service."""

try:
    import asyncio
except ImportError:
    import _asyncio as asyncio

try:
    import wifi
except ImportError:
    wifi = None

try:
    import mdns
except ImportError:
    mdns = None


# Module-level reference to the mDNS Server instance to prevent garbage collection
_mdns_instance = None


def normalize_hostname(raw):
    """Normalize raw input into a valid RFC 1123 DNS label for mDNS.

    Rules:
    1. None, empty, or all-whitespace -> default to 'mycro'.
    2. Strip leading/trailing whitespace and convert to lowercase.
    3. If ends with '.local', strip that suffix.
    4. Replace dots '.' and underscores '_' with hyphens '-'.
    5. Retain only characters in [a-z0-9-].
    6. Strip leading and trailing hyphens.
    7. Truncate to maximum 63 characters (RFC 1035 label limit).
    8. If empty or starting with non-alphanumeric, fallback to 'mycro'.
    """
    if not raw:
        return "mycro"
    clean = raw.strip().lower()
    if clean.endswith("."):
        clean = clean.rstrip(".")
    if clean.endswith(".local"):
        clean = clean[:-6].rstrip(".")
    clean = clean.replace(".", "-").replace("_", "-")
    buf = []
    for ch in clean:
        if ("a" <= ch <= "z") or ("0" <= ch <= "9") or ch == "-":
            buf.append(ch)
    clean = "".join(buf).strip("-")
    if not clean or not (("a" <= clean[0] <= "z") or ("0" <= clean[0] <= "9")):
        return "mycro"
    return clean[:63]


def init_mdns(radio, hostname_raw="mycro"):
    """Initialize mDNS service with normalized label and advertise HTTP port 80.

    Returns (mdns_server, normalized_label, full_domain).
    """
    global _mdns_instance
    label = normalize_hostname(hostname_raw)
    domain = f"{label}.local"
    if mdns is None or radio is None:
        return None, label, domain

    try:
        try:
            radio.hostname = label
        except Exception as e:
            print("Failed to set radio.hostname:", e)

        _mdns_instance = mdns.Server(radio)
        try:
            _mdns_instance.hostname = label
        except Exception:
            pass

        try:
            _mdns_instance.advertise_service(service_type="_http", protocol="_tcp", port=80)
        except Exception as e:
            print("Failed to advertise mDNS _http._tcp:", e)

        return _mdns_instance, label, domain
    except Exception as e:
        print("mDNS initialization failed:", e)
        return None, label, domain


async def wifi_connect(ssid, password, attempts=3, attempt_timeout_s=15.0, retry_delay_s=3.0):
    """Connect WiFi at runtime. Returns (is_connected, ip_address).

    Any failure leaves the device fully usable over USB (MSC / serial / HID).
    Modem sleep is disabled for maximum responsiveness on USB power.
    """
    if wifi is None:
        print("WiFi hardware not available in this environment")
        return False, None

    if not ssid:
        print("MYCRO_WIFI_SSID not set — offline mode (USB/serial/HID only)")
        return False, None

    for attempt in range(1, attempts + 1):
        try:
            print(f"Connecting WiFi '{ssid}' (attempt {attempt}/{attempts})...")
            try:
                wifi.radio.connect(ssid, password, timeout=attempt_timeout_s)
            except TypeError:
                wifi.radio.connect(ssid, password)  # older firmware without timeout kwarg

            try:
                # Modem sleep (default MIN) makes AP buffer inbound packets until DTIM:
                # ~350ms connection setup and sawtooth ping RTTs.
                wifi.radio.power_management = wifi.PowerManagement.NONE
            except Exception:
                pass

            ip = str(wifi.radio.ipv4_address)
            print("WiFi connected:", ip)
            return True, ip
        except Exception as e:
            print("WiFi connect failed:", e)
            if attempt < attempts:
                await asyncio.sleep(retry_delay_s)

    print("WiFi unavailable — offline mode (USB/serial/HID only)")
    return False, None


async def setup_network(ssid, password, hostname_raw="mycro"):
    """Setup WiFi and mDNS services.

    Returns dict with keys:
      - 'online': bool
      - 'ip': str or None
      - 'label': str
      - 'domain': str
      - 'mdns': mdns.Server or None
    """
    label = normalize_hostname(hostname_raw)
    domain = f"{label}.local"
    connected, ip = await wifi_connect(ssid, password)

    mdns_srv = None
    if connected and wifi is not None:
        mdns_srv, label, domain = init_mdns(wifi.radio, hostname_raw)
        print(f"mDNS active: http://{domain}/")

    return {
        "online": connected,
        "ip": ip,
        "label": label,
        "domain": domain,
        "mdns": mdns_srv,
    }
