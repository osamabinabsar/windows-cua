"""Byte-scan the OpenAI Codex "Owl" app bundle for a CSS cursor injector.

VS Code issue #239110 is titled "Activating display pointer trails makes
text editing pointer disappear", and the workflow's recon claimed the
Codex/Owl desktop app ships a document-wide `cursor: none !important`
injector that draws its own pointer in its place.

This checks that claim directly against the shipped bundle: it searches
app.asar for `cursor: none` / `cursor:none` rules and prints the
surrounding bytes so the rule can be read.

Only the app's own static assets are read -- no user data.

Read-only.
"""

from __future__ import annotations

import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CANDIDATES = [
    r"C:\Program Files\WindowsApps\OpenAI.Codex_26.1007.9446.0_x64__2p2nqsd0c76g0\app\resources\app.asar",
]

# Discover any Codex / Owl package that is actually installed.
WINAPPS = r"C:\Program Files\WindowsApps"
try:
    for name in sorted(os.listdir(WINAPPS)):
        if name.lower().startswith("openai.codex"):
            p = os.path.join(WINAPPS, name, "app", "resources", "app.asar")
            if os.path.isfile(p) and p not in CANDIDATES:
                CANDIDATES.append(p)
except PermissionError:
    # WindowsApps is ACL-protected; the explicit candidate path below
    # is still readable if the package is installed.
    pass

PATTERNS = [
    re.compile(rb"cursor\s*:\s*none", re.I),
    re.compile(rb"cursor\s*:\s*url", re.I),
    re.compile(rb"annotation-selection-cursor"),
    re.compile(rb"!important"),
]

CHUNK = 1 << 22
OVERLAP = 4096


def scan(path: str, pat: re.Pattern) -> list[tuple[int, bytes]]:
    hits = []
    with open(path, "rb") as fh:
        base = 0
        tail = b""
        while True:
            buf = fh.read(CHUNK)
            if not buf:
                break
            data = tail + buf
            for m in pat.finditer(data):
                off = base - len(tail) + m.start()
                if not hits or off > hits[-1][0]:
                    hits.append((off, m.group(0)))
            base += len(buf)
            tail = data[-OVERLAP:]
    return hits


def context(path: str, off: int, n: int = 320) -> str:
    with open(path, "rb") as fh:
        fh.seek(max(0, off - n // 2))
        raw = fh.read(n)
    s = raw.decode("utf-8", "replace")
    s = "".join(ch if 32 <= ord(ch) < 127 else "." for ch in s)
    return s


def main() -> int:
    print("=" * 78)
    print("OPENAI CODEX / OWL -- CSS cursor suppression scan")
    print("=" * 78)
    for path in CANDIDATES:
        if not os.path.isfile(path):
            print(f"\n  (not present) {path}")
            continue
        size = os.path.getsize(path)
        print(f"\n  {path}")
        print(f"    size: {size:,} bytes")
        for pat in PATTERNS:
            hits = scan(path, pat)
            label = pat.pattern.decode()
            if not hits:
                print(f"    {label:<32} -> 0 hits")
                continue
            print(f"    {label:<32} -> {len(hits)} hits")
            for off, matched in hits[:6]:
                print(f"        offset {off}  ({matched.decode('utf-8','replace')})")
                if "cursor" in label.lower():
                    print(f"          ...{context(path, off)}...")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
