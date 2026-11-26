"""TLS helpers: self-signed certificates for the server and runners, and the bind-safety rule."""
from __future__ import annotations

import datetime
import hashlib
import ipaddress
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def self_signed(dir_: Path, hosts: list[str], days: int = 825) -> tuple[Path, Path]:
    """Create (or reuse) cert.pem/key.pem in dir_ valid for `hosts` (DNS names or IPs)."""
    dir_.mkdir(parents=True, exist_ok=True)
    cert_p, key_p = dir_ / "cert.pem", dir_ / "key.pem"
    if cert_p.exists() and key_p.exists():
        cert = x509.load_pem_x509_certificate(cert_p.read_bytes())
        names = set(cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.DNSName))
        ips = {str(i) for i in cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.IPAddress)}
        if all(h in names or h in ips for h in hosts):
            return cert_p, key_p
    key = ec.generate_private_key(ec.SECP256R1())
    sans: list[x509.GeneralName] = []
    for h in hosts:
        try:
            sans.append(x509.IPAddress(ipaddress.ip_address(h)))
        except ValueError:
            sans.append(x509.DNSName(h))
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hosts[0])])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(minutes=5))
            .not_valid_after(now + datetime.timedelta(days=days))
            .add_extension(x509.SubjectAlternativeName(sans), critical=False)
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .sign(key, hashes.SHA256()))
    key_p.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                        serialization.NoEncryption()))
    key_p.chmod(0o600)
    cert_p.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return cert_p, key_p


def fingerprint(cert_p: Path) -> str:
    cert = x509.load_pem_x509_certificate(cert_p.read_bytes())
    return hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest()


def check_bind(host: str, tls: bool, insecure: bool) -> None:
    if host not in LOOPBACK and not tls and not insecure:
        raise SystemExit(f"refusing to serve plain HTTP on {host}: tokens and data would travel unencrypted.\n"
                         f"Use --self-signed HOSTNAME, --tls-cert/--tls-key, or a TLS proxy (deploy/Caddyfile) "
                         f"in front of a 127.0.0.1 bind. --insecure overrides.")
