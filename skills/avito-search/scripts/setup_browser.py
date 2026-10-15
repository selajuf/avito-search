#!/usr/bin/env python3
"""Install only the pinned GStack browse runtime into AVITO_HOME."""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from catalog import ROOT

UPSTREAM = "https://github.com/garrytan/gstack.git"
COMMIT = "47d639abffa882b8cbb641d7d1dedf08ecada9ec"
MANAGER_SHA256 = "f73fe272245ed0f54b65c47c2355c36e5403c787de37d8a5f3bab72ba43d38fb"
RUNTIME = ROOT / "runtime"
CHECKOUT = RUNTIME / "gstack"
CLI = CHECKOUT / "browse/dist/browse"
STAMP = RUNTIME / "browser-install.json"


def child_env():
    # Do not pass the agent's API credentials, browser state or preload flags.
    names = ("PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LC_ALL", "TMPDIR",
             "TMP", "TEMP", "DISPLAY", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS")
    env = {name: os.environ[name] for name in names if name in os.environ}
    env.update({"GSTACK_STATE_ROOT": str(ROOT / "browser/state"),
                "GSTACK_TEST_LEGACY_ROOT": str(ROOT / "browser/state"),
                "GSTACK_TELEMETRY_OFF": "1", "BROWSE_PARENT_PID": "0",
                "BROWSE_STATE_FILE": str(ROOT / "browser/.gstack/browse.json"),
                "CHROMIUM_PROFILE": str(ROOT / "browser/chromium-profile"),
                "PLAYWRIGHT_BROWSERS_PATH": str(RUNTIME / "chromium"),
                "BROWSE_PROXY_URL": "direct", "BROWSE_NO_AUTOSTART": "1",
                "BUN_INSTALL_CACHE_DIR": str(RUNTIME / "bun-cache")})
    return env


def run(*args, cwd=CHECKOUT):
    subprocess.run(list(map(str, args)), cwd=cwd, env=child_env(), check=True)


def isolate_extension(source):
    original = source.read_bytes()
    if hashlib.sha256(original).hexdigest() != MANAGER_SHA256:
        raise RuntimeError("Pinned browser-manager.ts differs; refusing an unverified runtime patch")
    text = original.decode()
    start = text.index("  private findExtensionPath(): string | null {\n")
    end = text.index("\n  /**\n   * Set the proxy config", start)
    patched = text[:start] + "  private findExtensionPath(): string | null {\n    return null;\n  }\n" + text[end:]
    source.write_text(patched)


def installed():
    if not STAMP.is_file() or not CLI.is_file():
        return False
    stamp = json.loads(STAMP.read_text())
    return (stamp.get("commit") == COMMIT
            and stamp.get("binary_sha256") == hashlib.sha256(CLI.read_bytes()).hexdigest()
            and stamp.get("manager_sha256") == hashlib.sha256((CHECKOUT / "browse/src/browser-manager.ts").read_bytes()).hexdigest())


def setup():
    if sys.platform == "win32":
        raise RuntimeError("Native Windows is not supported. Use WSL2 with a visible Linux desktop/WSLg.")
    for name in ("git", "bun"):
        if not shutil.which(name):
            raise RuntimeError(f"Install {name} first, then run setup_browser.py again")
    version = subprocess.check_output(["bun", "--version"], env=child_env(), text=True).strip()
    parsed = re.match(r"(\d+)\.(\d+)\.(\d+)", version)
    if not parsed or tuple(map(int, parsed.groups())) < (1, 3, 3):
        raise RuntimeError("Bun >=1.3.3 is required for safe compile flags; upstream recommends >=1.4.2")
    if tuple(map(int, parsed.groups())) < (1, 4, 2):
        print("Note: upstream's tested baseline is Bun >=1.4.2; this version meets its safe minimum.", file=sys.stderr)
    if installed():
        print(json.dumps({"installed": True, "commit": COMMIT, "binary": str(CLI)}, indent=2))
        return
    if CHECKOUT.exists():
        raise RuntimeError("Incomplete or modified runtime exists. Move AVITO_HOME/runtime/gstack aside and retry; it is never overwritten.")
    RUNTIME.mkdir(parents=True, exist_ok=True)
    run("git", "init", "--quiet", CHECKOUT, cwd=RUNTIME)
    run("git", "-c", "core.hooksPath=/dev/null", "fetch", "--depth", "1", UPSTREAM, COMMIT)
    run("git", "-c", "core.hooksPath=/dev/null", "checkout", "--detach", "--quiet", "FETCH_HEAD")
    actual = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=CHECKOUT, env=child_env(), text=True).strip()
    if actual != COMMIT:
        raise RuntimeError("Downloaded revision differs from the pinned public commit")
    isolate_extension(CHECKOUT / "browse/src/browser-manager.ts")
    run("bun", "install", "--frozen-lockfile", "--ignore-scripts", "--production")
    (CHECKOUT / "browse/dist").mkdir(parents=True, exist_ok=True)
    run("bun", "build", "--compile", "--no-compile-autoload-dotenv", "--no-compile-autoload-bunfig",
        "browse/src/cli.ts", "--outfile", "browse/dist/browse")
    run("bun", "node_modules/playwright/cli.js", "install", "chromium")
    stamp = {"commit": COMMIT, "upstream": UPSTREAM, "bun": version,
             "binary_sha256": hashlib.sha256(CLI.read_bytes()).hexdigest(),
             "manager_sha256": hashlib.sha256((CHECKOUT / "browse/src/browser-manager.ts").read_bytes()).hexdigest(),
             "patch": "Disable extension discovery; no global auth bootstrap or profile import"}
    STAMP.write_text(json.dumps(stamp, indent=2) + "\n")
    print(json.dumps({"installed": True, "commit": COMMIT, "binary": str(CLI)}, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    setup()


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
