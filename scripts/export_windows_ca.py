"""Export existing Windows server-auth trust plus Mozilla roots for local containers.

Does not install certificates or change system trust. Output stays git-ignored.
"""
from pathlib import Path
import ssl
import sys
import certifi

if sys.platform != "win32":
    raise SystemExit("This helper is for the existing Windows trust store only")
target = Path(__file__).resolve().parents[1] / ".local-tools/ca-bundle.pem"
target.parent.mkdir(exist_ok=True)
content = Path(certifi.where()).read_text(encoding="ascii")
count = 0
for raw, encoding, trust in ssl.enum_certificates("ROOT"):
    if encoding == "x509_asn" and (trust is True or "1.3.6.1.5.5.7.3.1" in trust):
        content += "\n" + ssl.DER_cert_to_PEM_cert(raw)
        count += 1
target.write_text(content, encoding="ascii")
print(f"Exported {count} existing trusted roots; TLS verification stays enabled")
