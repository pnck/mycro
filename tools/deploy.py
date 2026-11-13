#!/usr/bin/env python3
"""MYCRO unified deployer and release packager.

Single source of truth for device deployment and distribution packaging.
- Deploy mode: syncs curated files to a mounted CIRCUITPY drive with settings preservation.
- Package mode: bundles optimized files & frozen dependencies into a drop-in release ZIP.
"""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zipfile

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
LIB_DIR = SRC_DIR / "lib"
COMPILER_DIR = LIB_DIR / "compiler"
WEB_DIR = REPO_ROOT / "web"
OBS_SCRIPT = REPO_ROOT / "obs" / "obs_mycro.py"
REQ_DEVICE = REPO_ROOT / "requirements-device.txt"

MAX_GZ_BYTES = 51200


def log(msg: str) -> None:
    print(f"  • {msg}")


def log_success(msg: str) -> None:
    print(f"  + {msg}")


def log_warn(msg: str) -> None:
    print(f"  ! {msg}", file=sys.stderr)


def log_error(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Gates & Checks
# ---------------------------------------------------------------------------

def check_syntax() -> None:
    """Run mpy-cross syntax check if available."""
    if not shutil.which("mpy-cross"):
        log_warn("mpy-cross not found - skipping syntax gate (pip install mpy-cross)")
        return

    null_dev = "NUL" if sys.platform == "win32" else "/dev/null"
    py_files = [SRC_DIR / "code.py"] + list(LIB_DIR.glob("*.py"))
    if COMPILER_DIR.is_dir():
        py_files.extend(COMPILER_DIR.glob("*.py"))

    for f in py_files:
        if not f.is_file():
            continue
        res = subprocess.run(["mpy-cross", "-o", null_dev, str(f)], capture_output=True)
        if res.returncode != 0:
            log_error(f"mpy-cross failed on {f.relative_to(REPO_ROOT)}:\n{res.stderr.decode()}")
            sys.exit(1)
    log_success("mpy-cross syntax gate passed")


def check_and_build_web() -> None:
    """Ensure web bundle index.html.gz is up to date and within budget."""
    gz_file = SRC_DIR / "index.html.gz"

    if WEB_DIR.is_dir() and shutil.which("node"):
        check_script = WEB_DIR / "scripts" / "check.mjs"
        needs_build = True
        if check_script.is_file():
            res = subprocess.run(["node", str(check_script)], capture_output=True)
            needs_build = res.returncode != 0

        if needs_build:
            log("Web bundle outdated or missing - building via pnpm/vite...")
            pnpm = shutil.which("pnpm") or shutil.which("/opt/homebrew/bin/pnpm")
            if pnpm:
                build_res = subprocess.run([pnpm, "build"], cwd=WEB_DIR)
            else:
                build_res = subprocess.run(["node", "node_modules/vite/bin/vite.js", "build"], cwd=WEB_DIR)
            if build_res.returncode != 0:
                log_error("Web build failed.")
                sys.exit(1)
            log_success("Web bundle built successfully")
        else:
            log_success("Web bundle up-to-date")

    if not gz_file.is_file():
        log_error(f"Missing {gz_file.relative_to(REPO_ROOT)}; cannot proceed.")
        sys.exit(1)

    gz_size = gz_file.stat().st_size
    if gz_size > MAX_GZ_BYTES:
        log_error(f"Web bundle size ({gz_size} B) exceeds maximum budget of {MAX_GZ_BYTES} B.")
        sys.exit(1)
    log_success(f"Web bundle budget verified: {gz_size:,} B <= {MAX_GZ_BYTES:,} B")


def sweep_apple_double(target_dir: Path) -> None:
    """Sweep macOS AppleDouble metadata files (._*)."""
    for p in target_dir.rglob("._*"):
        try:
            if p.is_file():
                p.unlink()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Core File Syncing
# ---------------------------------------------------------------------------

def sync_core_files(target_dir: Path) -> list[str]:
    """Copy all core project files into target_dir. Returns relative paths of deployed files."""
    deployed: list[str] = []

    # 1. code.py
    shutil.copy2(SRC_DIR / "code.py", target_dir / "code.py")
    deployed.append("code.py")

    # 2. index.html.gz (remove any uncompressed index.html)
    shutil.copy2(SRC_DIR / "index.html.gz", target_dir / "index.html.gz")
    deployed.append("index.html.gz")
    uncompressed = target_dir / "index.html"
    if uncompressed.is_file():
        uncompressed.unlink()
        log("Removed uncompressed index.html")

    # 3. lib/*.py
    target_lib = target_dir / "lib"
    target_lib.mkdir(parents=True, exist_ok=True)
    for f in LIB_DIR.glob("*.py"):
        shutil.copy2(f, target_lib / f.name)
        deployed.append(f"lib/{f.name}")

    # 4. lib/compiler/*.py
    if COMPILER_DIR.is_dir():
        target_compiler = target_lib / "compiler"
        target_compiler.mkdir(parents=True, exist_ok=True)
        for f in COMPILER_DIR.glob("*.py"):
            shutil.copy2(f, target_compiler / f.name)
            deployed.append(f"lib/compiler/{f.name}")

    # 5. obs/obs_mycro.py
    if OBS_SCRIPT.is_file():
        target_obs = target_dir / "obs"
        target_obs.mkdir(parents=True, exist_ok=True)
        shutil.copy2(OBS_SCRIPT, target_obs / OBS_SCRIPT.name)
        deployed.append(f"obs/{OBS_SCRIPT.name}")

    sweep_apple_double(target_dir)
    return deployed


# ---------------------------------------------------------------------------
# Deploy Mode (Physical Device)
# ---------------------------------------------------------------------------

def deploy_to_device(target_dir: Path) -> None:
    """Deploy curated files to a mounted CIRCUITPY drive."""
    if not target_dir.is_dir():
        log_error(f"CIRCUITPY drive not found at {target_dir}")
        sys.exit(1)

    print(f"\nDeploying MYCRO to {target_dir}...")
    check_syntax()
    check_and_build_web()

    deployed_files = sync_core_files(target_dir)

    # Manifest cleanup: remove stale files from older deploys
    manifest_file = target_dir / ".mycro_files"
    if manifest_file.is_file():
        try:
            old_files = manifest_file.read_text().splitlines()
            for old in old_files:
                old = old.strip()
                if old and old not in deployed_files:
                    stale = target_dir / old
                    if stale.is_file():
                        stale.unlink()
                        log(f"Removed stale {old}")
        except Exception as e:
            log_warn(f"Failed to read manifest: {e}")
    manifest_file.write_text("\n".join(deployed_files) + "\n")

    # Remove shadowing .mpy files in lib/
    for mpy in (target_dir / "lib").glob("*.mpy"):
        base_py = SRC_DIR / "lib" / f"{mpy.stem}.py"
        if base_py.is_file():
            mpy.unlink()
            log(f"Removed shadowing lib/{mpy.name}")

    # settings.toml: preserve existing variables, append missing template keys
    settings_file = target_dir / "settings.toml"
    existing_content = settings_file.read_text() if settings_file.is_file() else ""

    append_lines: list[str] = []
    if "MYCRO_WIFI_SSID" not in existing_content:
        append_lines.append(
            "\n# --- MYCRO ---\n# WiFi credentials use project-specific keys (NOT built-in CIRCUITPY_WIFI_*)\n"
        )
    for key in ("MYCRO_WIFI_SSID", "MYCRO_WIFI_PASSWORD", "MYCRO_TOKEN"):
        if not any(line.strip().startswith(key) for line in existing_content.splitlines()):
            append_lines.append(f'{key} = ""')
            log_success(f'settings.toml: appended {key} = "" (please configure)')

    if append_lines:
        with settings_file.open("a", encoding="utf-8") as f:
            f.write("\n" + "\n".join(append_lines) + "\n")

    # Install device libraries via circup if available
    if shutil.which("circup") and REQ_DEVICE.is_file():
        log("Checking device libraries via circup...")
        subprocess.run(["circup", "--path", str(target_dir), "install", "-r", str(REQ_DEVICE)])
    else:
        log_warn("circup not found - please verify required CircuitPython libraries are installed.")

    sweep_apple_double(target_dir)
    print(f"\n[DONE] Deployed {len(deployed_files)} files successfully to {target_dir}\n")


# ---------------------------------------------------------------------------
# Package Mode (Drop-in ZIP Bundle)
# ---------------------------------------------------------------------------

def package_bundle(version: str, output_dir: Path) -> None:
    """Create an optimized drop-in ZIP bundle for release distribution."""
    print(f"\nPackaging MYCRO release bundle [{version}]...")
    check_syntax()
    check_and_build_web()

    output_dir.mkdir(parents=True, exist_ok=True)
    zip_filename = f"mycro-{version}-circuitpy.zip"
    zip_path = output_dir / zip_filename

    with tempfile.TemporaryDirectory(prefix="mycro_pkg_") as tmp_dir:
        staging_dir = Path(tmp_dir)

        # 1. Sync project files
        deployed_files = sync_core_files(staging_dir)

        # 2. Add clean settings.toml.example
        settings_example = staging_dir / "settings.toml.example"
        settings_example.write_text(
            '# MYCRO Configuration Template\n'
            '# Copy this file to settings.toml on your CIRCUITPY drive and set your credentials.\n'
            '\n'
            'MYCRO_WIFI_SSID = ""\n'
            'MYCRO_WIFI_PASSWORD = ""\n'
            'MYCRO_TOKEN = ""\n'
        )
        deployed_files.append("settings.toml.example")

        # 3. Pull frozen bundle dependencies via circup if available
        if shutil.which("circup") and REQ_DEVICE.is_file():
            log("Pulling frozen CircuitPython 10.x bundle dependencies via circup...")
            cmd = [
                "circup",
                "--path", str(staging_dir),
                "--board-id", "espressif_esp32s3_devkitc_1_n8r8",
                "--cpy-version", "10.0.0",
                "install", "-r", str(REQ_DEVICE),
            ]
            res = subprocess.run(cmd, capture_output=True, text=True)
            if res.returncode != 0:
                log_warn(f"circup bundle install notice:\n{res.stderr or res.stdout}")
            else:
                log_success("CircuitPython bundle libraries frozen into lib/")
        else:
            log_warn("circup not available; bundle dependencies omitted from lib/")

        sweep_apple_double(staging_dir)

        # 4. Create ZIP archive
        log(f"Assembling archive {zip_filename}...")
        total_files = 0
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, _, files in os.walk(staging_dir):
                for f in files:
                    full_p = Path(root) / f
                    rel_p = full_p.relative_to(staging_dir)
                    zf.write(full_p, rel_p.as_posix())
                    total_files += 1

    # 5. Compute SHA-256 Checksum
    hasher = hashlib.sha256()
    with zip_path.open("rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    digest = hasher.hexdigest()

    checksum_file = output_dir / "SHA256SUMS.txt"
    checksum_line = f"{digest}  {zip_filename}\n"
    checksum_file.write_text(checksum_line)

    zip_kb = zip_path.stat().st_size / 1024
    print(f"\n[DONE] Package created successfully:")
    print(f"  Archive:  {zip_path} ({zip_kb:.1f} KB, {total_files} files)")
    print(f"  Checksum: {checksum_file} ({digest[:16]}...)")


# ---------------------------------------------------------------------------
# CLI Entry Point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="MYCRO unified deployer and release packager."
    )
    parser.add_argument(
        "--package", "-p",
        nargs="?",
        const="latest",
        metavar="VERSION",
        help="Package a drop-in release ZIP bundle instead of deploying to hardware.",
    )
    parser.add_argument(
        "--output-dir", "-o",
        type=Path,
        default=REPO_ROOT / "dist",
        help="Output directory for packaged release artifacts (default: dist/).",
    )
    parser.add_argument(
        "--target", "-t",
        type=Path,
        default=Path(os.environ.get("CIRCUITPY", "/Volumes/CIRCUITPY")),
        help="Target mounted CIRCUITPY directory (default: $CIRCUITPY or /Volumes/CIRCUITPY).",
    )

    args = parser.parse_args()

    if args.package is not None:
        version = args.package if args.package != "latest" else "latest"
        package_bundle(version, args.output_dir)
    else:
        deploy_to_device(args.target)


if __name__ == "__main__":
    main()

