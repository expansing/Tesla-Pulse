"""Manage the external Tesla Vehicle Command proxy add-on."""

from __future__ import annotations

import asyncio
import datetime
import ipaddress
import logging
import ssl
from pathlib import Path

import aiohttp
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.x509.oid import NameOID
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .addon_hosts import async_resolve_addon_hosts, proxy_host
from .const import (
    CONF_PRIVATE_KEY_PATH,
    CONF_TELEMETRY_HOSTNAME,
    CONF_VEHICLES,
    DOMAIN,
    PROXY_PORT,
)

_LOGGER = logging.getLogger(__name__)

_COMMAND_KEY_FILE = "proxy-command-key.pem"
_COMMAND_PUBLIC_KEY_FILE = "proxy-command-public-key.pem"
_CERT_FILE = "proxy-cert.pem"
_TLS_KEY_FILE = "proxy-key.pem"
_CA_FILE = "proxy-ca.pem"
_TELEMETRY_CA_FILE = "telemetry-ca.pem"
_TELEMETRY_CERT_FILE = "telemetry-cert.pem"
_TELEMETRY_KEY_FILE = "telemetry-key.pem"


class ProxyManager:
    """Prepare files for and monitor the Tesla Vehicle Command proxy add-on."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the proxy manager."""
        self.hass = hass
        self.entry = entry
        self._cert_path: Path | None = None
        self._key_path: Path | None = None
        self._ca_path: Path | None = None
        self._telemetry_ca_path: Path | None = None
        self._running = False

    @property
    def is_running(self) -> bool:
        """Return whether the external proxy is reachable."""
        return self._running

    @property
    def cert_path(self) -> Path | None:
        """Return the CA certificate used to validate the proxy."""
        return self._ca_path

    @property
    def telemetry_ca_path(self) -> Path | None:
        """Return the CA certificate registered for Fleet Telemetry."""
        return self._telemetry_ca_path

    async def async_start(self) -> None:
        """Prepare proxy files and wait for the external add-on to start."""
        if self.is_running:
            return

        vehicles = self.entry.data.get(CONF_VEHICLES, [])
        private_key_path = (
            vehicles[0].get(CONF_PRIVATE_KEY_PATH) if vehicles else None
        )
        if not private_key_path or not Path(private_key_path).is_file():
            raise RuntimeError(f"Private key not found at {private_key_path}")

        await async_resolve_addon_hosts(self.hass)
        config_dir = Path(self.hass.config.path(DOMAIN))
        self._cert_path, self._key_path, self._ca_path = (
            await self.hass.async_add_executor_job(
                self._prepare_proxy_files, config_dir, Path(private_key_path)
            )
        )

        telemetry_hostname = self.entry.options.get(CONF_TELEMETRY_HOSTNAME, "")
        if telemetry_hostname:
            self._telemetry_ca_path = await self.hass.async_add_executor_job(
                self._ensure_telemetry_certificates,
                config_dir,
                telemetry_hostname,
            )

        await self._wait_for_ready()

    async def async_stop(self) -> None:
        """Mark the external proxy unavailable without stopping its add-on."""
        self._running = False

    async def _wait_for_ready(self, timeout: float = 30.0) -> None:
        """Wait for the external proxy add-on health endpoint."""
        if not self._ca_path:
            raise RuntimeError("Proxy CA certificate is not initialized")

        ssl_context = await self.hass.async_add_executor_job(
            self._create_ssl_context, self._ca_path
        )
        session = async_get_clientsession(self.hass)
        deadline = asyncio.get_running_loop().time() + timeout
        last_error: Exception | None = None

        while asyncio.get_running_loop().time() < deadline:
            try:
                async with session.get(
                    f"https://{proxy_host()}:{PROXY_PORT}/health",
                    ssl=ssl_context,
                    timeout=aiohttp.ClientTimeout(total=2),
                ) as response:
                    self._running = True
                    _LOGGER.info(
                        "Tesla Vehicle Command proxy is ready at %s:%s (%s)",
                        proxy_host(),
                        PROXY_PORT,
                        response.status,
                    )
                    return
            except (aiohttp.ClientError, asyncio.TimeoutError) as err:
                last_error = err
                pass

            await asyncio.sleep(1)

        message = (
            "Tesla Vehicle Command proxy add-on is not reachable. "
            "Install and start the local tesla_vehicle_command_proxy add-on."
        )
        if last_error:
            message = f"{message} Last connection error: {last_error}"
        raise RuntimeError(message) from last_error

    @staticmethod
    def _create_ssl_context(ca_path: Path) -> ssl.SSLContext:
        """Build the proxy TLS context outside Home Assistant's event loop."""
        ssl_context = ssl.create_default_context()
        ssl_context.load_verify_locations(ca_path)
        return ssl_context

    @staticmethod
    def _prepare_proxy_files(
        config_dir: Path, private_key_path: Path
    ) -> tuple[Path, Path, Path]:
        """Create TLS material and normalize the command key for the add-on."""
        config_dir.mkdir(parents=True, exist_ok=True)
        cert_path, key_path, ca_path = ProxyManager._ensure_certificates(config_dir)

        try:
            private_key = serialization.load_pem_private_key(
                private_key_path.read_bytes(), password=None
            )
        except ValueError as err:
            raise RuntimeError(
                f"Command key at {private_key_path} is not valid PEM"
            ) from err

        if not isinstance(private_key, ec.EllipticCurvePrivateKey) or not isinstance(
            private_key.curve, ec.SECP256R1
        ):
            raise RuntimeError(
                "Command key must be an unencrypted NIST P-256 EC private key"
            )

        command_key_path = config_dir / _COMMAND_KEY_FILE
        command_key_path.write_bytes(
            private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )
        command_key_path.chmod(0o600)
        (config_dir / _COMMAND_PUBLIC_KEY_FILE).write_bytes(
            private_key.public_key().public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            )
        )

        return cert_path, key_path, ca_path

    @staticmethod
    def _ensure_certificates(config_dir: Path) -> tuple[Path, Path, Path]:
        """Generate or load the localhost TLS certificates for the proxy."""
        cert_path = config_dir / _CERT_FILE
        key_path = config_dir / _TLS_KEY_FILE
        ca_path = config_dir / _CA_FILE

        if (
            cert_path.is_file()
            and key_path.is_file()
            and ca_path.is_file()
            and ProxyManager._certificate_matches_proxy_host(cert_path)
        ):
            return cert_path, key_path, ca_path

        now = datetime.datetime.now(datetime.timezone.utc)
        ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        ca_name = x509.Name(
            [x509.NameAttribute(NameOID.COMMON_NAME, "Tesla Vehicle Command CA")]
        )
        ca_cert = (
            x509.CertificateBuilder()
            .subject_name(ca_name)
            .issuer_name(ca_name)
            .public_key(ca_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now)
            .not_valid_after(now + datetime.timedelta(days=3650))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .add_extension(
                x509.KeyUsage(
                    digital_signature=False,
                    content_commitment=False,
                    key_encipherment=False,
                    data_encipherment=False,
                    key_agreement=False,
                    key_cert_sign=True,
                    crl_sign=True,
                    encipher_only=False,
                    decipher_only=False,
                ),
                critical=True,
            )
            .add_extension(
                x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()),
                critical=False,
            )
            .sign(ca_key, hashes.SHA256())
        )

        server_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        server_name = x509.Name(
            [x509.NameAttribute(NameOID.COMMON_NAME, "localhost")]
        )
        server_cert = (
            x509.CertificateBuilder()
            .subject_name(server_name)
            .issuer_name(ca_name)
            .public_key(server_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now)
            .not_valid_after(now + datetime.timedelta(days=365))
            .add_extension(
                x509.SubjectAlternativeName(
                    [
                        x509.DNSName("localhost"),
                        x509.DNSName(proxy_host()),
                        x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
                    ]
                ),
                critical=False,
            )
            .add_extension(
                x509.ExtendedKeyUsage(
                    [x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]
                ),
                critical=True,
            )
            .add_extension(
                x509.AuthorityKeyIdentifier.from_issuer_public_key(
                    ca_key.public_key()
                ),
                critical=False,
            )
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True,
                    content_commitment=False,
                    key_encipherment=True,
                    data_encipherment=False,
                    key_agreement=False,
                    key_cert_sign=False,
                    crl_sign=False,
                    encipher_only=False,
                    decipher_only=False,
                ),
                critical=True,
            )
            .sign(ca_key, hashes.SHA256())
        )

        ca_path.write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))
        cert_path.write_bytes(server_cert.public_bytes(serialization.Encoding.PEM))
        key_path.write_bytes(
            server_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )
        key_path.chmod(0o600)
        cert_path.chmod(0o644)
        ca_path.chmod(0o644)

        return cert_path, key_path, ca_path

    @staticmethod
    def _certificate_matches_proxy_host(cert_path: Path) -> bool:
        """Return whether an existing proxy certificate covers its hostname."""
        try:
            certificate = x509.load_pem_x509_certificate(cert_path.read_bytes())
            subject_alt_name = certificate.extensions.get_extension_for_class(
                x509.SubjectAlternativeName
            ).value
            authority_key_identifier = certificate.extensions.get_extension_for_class(
                x509.AuthorityKeyIdentifier
            ).value
        except (ValueError, x509.ExtensionNotFound):
            return False

        return (
            proxy_host() in subject_alt_name.get_values_for_type(x509.DNSName)
            and authority_key_identifier.key_identifier is not None
        )

    @staticmethod
    def _ensure_telemetry_certificates(config_dir: Path, hostname: str) -> Path:
        """Create Fleet Telemetry server certificates for the public hostname."""
        ca_path = config_dir / _TELEMETRY_CA_FILE
        cert_path = config_dir / _TELEMETRY_CERT_FILE
        key_path = config_dir / _TELEMETRY_KEY_FILE

        if (
            ca_path.is_file()
            and cert_path.is_file()
            and key_path.is_file()
            and ProxyManager._certificate_matches_hostname(cert_path, hostname)
        ):
            return ca_path

        now = datetime.datetime.now(datetime.timezone.utc)
        ca_key = ec.generate_private_key(ec.SECP256R1())
        ca_name = x509.Name(
            [x509.NameAttribute(NameOID.COMMON_NAME, "Tesla Fleet Telemetry CA")]
        )
        ca_cert = (
            x509.CertificateBuilder()
            .subject_name(ca_name)
            .issuer_name(ca_name)
            .public_key(ca_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now)
            .not_valid_after(now + datetime.timedelta(days=3650))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .add_extension(
                x509.KeyUsage(
                    digital_signature=False,
                    content_commitment=False,
                    key_encipherment=False,
                    data_encipherment=False,
                    key_agreement=False,
                    key_cert_sign=True,
                    crl_sign=True,
                    encipher_only=False,
                    decipher_only=False,
                ),
                critical=True,
            )
            .add_extension(
                x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()),
                critical=False,
            )
            .sign(ca_key, hashes.SHA256())
        )
        server_key = ec.generate_private_key(ec.SECP256R1())
        server_cert = (
            x509.CertificateBuilder()
            .subject_name(
                x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)])
            )
            .issuer_name(ca_name)
            .public_key(server_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now)
            .not_valid_after(now + datetime.timedelta(days=3650))
            .add_extension(
                x509.SubjectAlternativeName([x509.DNSName(hostname)]),
                critical=False,
            )
            .add_extension(
                x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]),
                critical=True,
            )
            .add_extension(
                x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
                critical=False,
            )
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True,
                    content_commitment=False,
                    key_encipherment=False,
                    data_encipherment=False,
                    key_agreement=True,
                    key_cert_sign=False,
                    crl_sign=False,
                    encipher_only=False,
                    decipher_only=False,
                ),
                critical=True,
            )
            .sign(ca_key, hashes.SHA256())
        )
        ca_path.write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))
        cert_path.write_bytes(server_cert.public_bytes(serialization.Encoding.PEM))
        key_path.write_bytes(
            server_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )
        ca_path.chmod(0o644)
        cert_path.chmod(0o644)
        key_path.chmod(0o600)
        return ca_path

    @staticmethod
    def _certificate_matches_hostname(cert_path: Path, hostname: str) -> bool:
        """Return whether an existing certificate covers the telemetry hostname."""
        try:
            certificate = x509.load_pem_x509_certificate(cert_path.read_bytes())
            subject_alt_name = certificate.extensions.get_extension_for_class(
                x509.SubjectAlternativeName
            ).value
        except (ValueError, x509.ExtensionNotFound):
            return False

        return hostname in subject_alt_name.get_values_for_type(x509.DNSName)
