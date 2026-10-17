#!/bin/sh
# Deploy to a mounted CIRCUITPY drive.
# Usage: CIRCUITPY=/Volumes/CIRCUITPY ./tools/deploy.sh
set -e

# Don't let macOS sprinkle AppleDouble (._*) metadata onto the FAT drive
export COPYFILE_DISABLE=1

MNT="${CIRCUITPY:-/Volumes/CIRCUITPY}"
SRC="$(cd "$(dirname "$0")/.." && pwd)/src"

[ -d "$MNT" ] || { echo "CIRCUITPY drive not found at $MNT" >&2; exit 1; }

# Syntax gate: mpy-cross (pip install mpy-cross) uses the real CircuitPython
# parser — it catches CP-incompatible syntax that CPython happily accepts
# (e.g. f-string brace escapes). Skipped with a note if not installed.
if command -v mpy-cross >/dev/null 2>&1; then
    for f in "$SRC/code.py" "$SRC/lib/"*.py; do
        mpy-cross -o /dev/null "$f" || exit 1
    done
    echo "  + mpy-cross syntax gate passed"
else
    echo "NOTE: mpy-cross not found - skipping CP syntax gate (pip install mpy-cross)" >&2
fi

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# --- Web bundle freshness gate: build only if web/ sources have changed ---
if [ -d "$ROOT/web" ]; then
    NODE_BIN=""
    if command -v node >/dev/null 2>&1; then
        NODE_BIN="node"
    elif [ -x "/opt/homebrew/bin/node" ]; then
        NODE_BIN="/opt/homebrew/bin/node"
    fi

    if [ -n "$NODE_BIN" ]; then
        if ! "$NODE_BIN" "$ROOT/web/scripts/check.mjs" >/dev/null 2>&1; then
            echo "  • web bundle outdated - building via vite..."
            if command -v pnpm >/dev/null 2>&1; then
                (cd "$ROOT/web" && pnpm build) || exit 1
            elif [ -x "/Users/pnck/Library/pnpm/pnpm" ]; then
                (cd "$ROOT/web" && /Users/pnck/Library/pnpm/pnpm build) || exit 1
            else
                (cd "$ROOT/web" && "$NODE_BIN" node_modules/vite/bin/vite.js build) || exit 1
            fi
        else
            echo "  + web bundle up-to-date - skipping build"
        fi
    elif [ ! -f "$SRC/index.html" ]; then
        echo "ERROR: src/index.html missing and node is not available to build it." >&2
        exit 1
    else
        echo "NOTE: node not found - skipping web freshness check, using existing src/index.html" >&2
    fi
fi

# --- project files: exact-set sync (stale files from older deploys removed) ---
NEW_FILES="code.py index.html"
cp "$SRC/code.py" "$MNT/code.py"
cp "$SRC/index.html" "$MNT/index.html"
mkdir -p "$MNT/lib"
for f in "$SRC/lib/"*.py; do
    cp "$f" "$MNT/lib/"
    NEW_FILES="$NEW_FILES lib/$(basename "$f")"
done

# Remove files previous deploys shipped but this repo no longer ships
MANIFEST="$MNT/.mycro_files"
if [ -f "$MANIFEST" ]; then
    while IFS= read -r old; do
        case " $NEW_FILES " in
            *" $old "*) ;;
            *) [ ! -f "$MNT/$old" ] || { rm "$MNT/$old"; echo "  - removed stale $old"; } ;;
        esac
    done < "$MANIFEST"
fi
printf '%s\n' $NEW_FILES > "$MANIFEST"

# A stale .mpy would shadow our .py sources (CircuitPython imports .mpy first)
for f in "$MNT/lib/"*.mpy; do
    [ -e "$f" ] || continue
    base="$(basename "$f" .mpy)"
    if [ -f "$SRC/lib/$base.py" ]; then
        rm "$f"
        echo "  - removed shadowing lib/$base.mpy"
    fi
done

# --- settings.toml: preserve existing vars; append missing template keys ---
SETTINGS="$MNT/settings.toml"
touch "$SETTINGS"

if ! grep -q "MYCRO_WIFI_SSID" "$SETTINGS"; then
    printf '\n# --- MYCRO ---\n# WiFi credentials use project-specific keys (NOT the built-in\n# CIRCUITPY_WIFI_*: a pre-boot connect failure would hang the device and take\n# away even the CIRCUITPY drive)\n' >> "$SETTINGS"
fi
for KEY in MYCRO_WIFI_SSID MYCRO_WIFI_PASSWORD MYCRO_TOKEN; do
    if ! grep -q "^$KEY" "$SETTINGS"; then
        echo "$KEY = \"\"" >> "$SETTINGS"
        echo "  + settings.toml: appended $KEY = \"\" (please configure)"
    fi
done
if grep -q "^CIRCUITPY_WIFI_SSID" "$SETTINGS"; then
    echo "NOTE: settings.toml still contains CIRCUITPY_WIFI_SSID - the built-in pre-boot WiFi connect stays active;"
    echo "      comment/remove that line manually to fully avoid boot hangs on network outage (this script never touches existing vars)."
fi

# Device-side libraries (pinned via circup; see requirements-device.txt)
# NOTE: on CircuitPython 10.x the firmware ships only the `_asyncio` C core;
# the user-facing `asyncio` package comes from the bundle library (per the 10.0.0
# release notes). Missing it causes: ImportError: no module named 'asyncio'.
# circup install also updates outdated listed libs to the latest
# firmware-compatible bundle version. Libraries NOT listed here are never
# touched; audit leftovers with `circup list` / `circup uninstall <name>`.
if command -v circup >/dev/null 2>&1; then
    circup install asyncio adafruit_ticks adafruit_hid adafruit_httpserver
else
    echo "WARNING: circup not found — install adafruit_hid + adafruit_httpserver manually" >&2
fi

# Sweep macOS AppleDouble metadata (._*): useless on device, ~4KB each on FAT
find "$MNT" -name "._*" -type f -delete 2>/dev/null || true

sync
echo "Deployed to $MNT"
