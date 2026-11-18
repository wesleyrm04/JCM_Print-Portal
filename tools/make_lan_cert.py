"""Make a self-signed HTTPS certificate for the shop network, so the staff PIN and session cookie are no longer sent
in clear text over the Wi-Fi.   Run once:   python tools/make_lan_cert.py  [ip-or-name ...]

  python tools/make_lan_cert.py 10.0.4.37 jcm-portal.lan

Writes data/lan_cert.pem and data/lan_key.pem, then start the portal with  JCM_TLS_PORTS=8443  and open
https://10.0.4.37:8443/admin on the staff devices (the browser warns once about the self-signed certificate:
choose Advanced > Continue, or install lan_cert.pem on that device as a trusted certificate).
Needs either the 'cryptography' package (pip install cryptography) or the 'openssl' program."""
import datetime
import ipaddress
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from portal import settings  # noqa: E402


def main():
    names = sys.argv[1:] or ["localhost", "127.0.0.1"]
    out = settings.data_dir()
    cert, key = out / "lan_cert.pem", out / "lan_key.pem"
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
        k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        san = []
        for n in names:
            try:
                san.append(x509.IPAddress(ipaddress.ip_address(n)))
            except ValueError:
                san.append(x509.DNSName(n))
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "JCM Print Portal")])
        now = datetime.datetime.now(datetime.timezone.utc)
        c = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(k.public_key())
             .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(days=1))
             .not_valid_after(now + datetime.timedelta(days=3650))
             .add_extension(x509.SubjectAlternativeName(san), critical=False)
             .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
             .sign(k, hashes.SHA256()))
        key.write_bytes(k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                        serialization.NoEncryption()))
        cert.write_bytes(c.public_bytes(serialization.Encoding.PEM))
    except ImportError:
        exe = shutil.which("openssl")
        if not exe:
            sys.exit("Need either:  pip install cryptography   or the 'openssl' program on PATH.")
        san = ",".join(("IP:" if n.replace(".", "").isdigit() or ":" in n else "DNS:") + n for n in names)
        subprocess.run([exe, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key), "-out", str(cert),
                        "-days", "3650", "-subj", "/CN=JCM Print Portal", "-addext", f"subjectAltName={san}"], check=True)
    from portal import auth
    auth.lock_down(key)
    print(f"Wrote {cert}\nWrote {key}  (keep this file private)\nCovers: {', '.join(names)}")


if __name__ == "__main__":
    main()
