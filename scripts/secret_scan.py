"""Scan versioned/new non-ignored files without printing credential material."""
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
files = subprocess.check_output(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT).decode().split("\0")
secrets = []
env = ROOT / ".env"
if env.exists():
    for line in env.read_text(encoding="utf-8-sig").splitlines():
        name, separator, value = line.partition("=")
        if separator and name.strip() in {"OPENAI_API_KEY", "LITEAPI_KEY", "YANDEX_RASP_API_KEY"}:
            value = value.strip().strip('"\'')
            if len(value) >= 16:
                secrets.append(value.encode())
failures = []
for name in set(files) - {""}:
    path = ROOT / name
    if not path.is_file():
        continue
    if path.name in {".env", ".env.local", ".env.production"}:
        failures.append(name)
        continue
    body = path.read_bytes()
    if any(secret in body for secret in secrets) or re.search(rb"sk-(?:proj-)?[A-Za-z0-9_-]{35,}", body):
        failures.append(name)
if failures:
    print("Credential material found in: " + ", ".join(sorted(failures)))
    raise SystemExit(1)
print(f"Secret scan passed: {len(set(files) - {''})} files; no credential values printed")
