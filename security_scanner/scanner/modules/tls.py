"""TLS/SSL configuration checks using only the standard library."""

from __future__ import annotations

import datetime as _dt
import socket
import ssl

from .base import Module, Severity, Confidence


# protocols we consider obsolete/insecure if the server negotiates them
_LEGACY_PROTOCOLS = [
    ("SSLv23/TLSv1.0", ssl.TLSVersion.TLSv1),
    ("TLSv1.1", ssl.TLSVersion.TLSv1_1),
]


class TlsModule(Module):
    name = "tls"
    description = "Certificate validity and TLS protocol/cipher hygiene"

    def run(self) -> None:
        if self.ctx.scheme != "https":
            # Still worth noting the site is plain HTTP.
            if self.ctx.port in (80,):
                self.report(
                    title="Site served over plaintext HTTP",
                    severity=Severity.MEDIUM,
                    category="Transport Security",
                    description="Traffic is unencrypted and can be read or modified by any "
                    "network intermediary.",
                    remediation="Serve the site over HTTPS and redirect HTTP → HTTPS.",
                    confidence=Confidence.CONFIRMED,
                )
            return

        host, port = self.ctx.host, self.ctx.port
        self._check_certificate(host, port)
        self._check_protocols(host, port)
        self._check_http_redirect()

    # -----------------------------------------------------------------
    def _connect(self, host, port, context, timeout=8):
        sock = socket.create_connection((host, port), timeout=timeout)
        try:
            ssock = context.wrap_socket(sock, server_hostname=host)
            return sock, ssock
        except Exception:
            sock.close()
            raise

    def _check_certificate(self, host, port) -> None:
        # Validating context first (proper trust + hostname check).
        ctx = ssl.create_default_context()
        try:
            sock, ssock = self._connect(host, port, ctx)
        except ssl.SSLCertVerificationError as e:
            reason = str(e)
            sev = Severity.HIGH
            if "self-signed" in reason.lower() or "self signed" in reason.lower():
                title = "Self-signed / untrusted TLS certificate"
            elif "hostname mismatch" in reason.lower() or "doesn't match" in reason.lower():
                title = "TLS certificate hostname mismatch"
            elif "expired" in reason.lower():
                title = "Expired TLS certificate"
            else:
                title = "TLS certificate verification failed"
            self.report(
                title=title,
                severity=sev,
                category="Transport Security",
                evidence=reason,
                description="The certificate does not validate against the system trust "
                "store / hostname. Browsers will warn users and it enables MITM.",
                remediation="Install a valid certificate from a trusted CA (e.g. Let's Encrypt) "
                "matching the hostname.",
                confidence=Confidence.CONFIRMED,
            )
            # continue to inspect the cert with a non-validating context
            self._inspect_cert_insecure(host, port)
            return
        except Exception as e:
            self.log.status(f"tls: could not establish validated TLS: {e}")
            self._inspect_cert_insecure(host, port)
            return

        try:
            cert = ssock.getpeercert()
            self._eval_cert_dates(cert)
        finally:
            try:
                ssock.close()
            except Exception:
                pass
            try:
                sock.close()
            except Exception:
                pass

    def _inspect_cert_insecure(self, host, port) -> None:
        ctx = ssl._create_unverified_context()
        try:
            sock, ssock = self._connect(host, port, ctx)
        except Exception:
            return
        try:
            cert = ssock.getpeercert()
            if cert:
                self._eval_cert_dates(cert)
        finally:
            try:
                ssock.close()
            except Exception:
                pass
            try:
                sock.close()
            except Exception:
                pass

    def _eval_cert_dates(self, cert) -> None:
        if not cert:
            return
        not_after = cert.get("notAfter")
        if not not_after:
            return
        try:
            expires = _dt.datetime.strptime(not_after, "%b %d %H:%M:%S %Y %Z")
        except Exception:
            return
        now = _dt.datetime.utcnow()
        days = (expires - now).days
        if days < 0:
            self.report(
                title="TLS certificate is expired",
                severity=Severity.HIGH,
                category="Transport Security",
                evidence=f"notAfter={not_after} ({-days} days ago)",
                description="An expired certificate breaks trust and triggers browser errors.",
                remediation="Renew the certificate and automate renewal.",
                confidence=Confidence.CONFIRMED,
            )
        elif days < 15:
            self.report(
                title="TLS certificate expiring very soon",
                severity=Severity.MEDIUM,
                category="Transport Security",
                evidence=f"notAfter={not_after} ({days} days left)",
                description="The certificate expires imminently.",
                remediation="Renew now; automate renewal to avoid outages.",
                confidence=Confidence.CONFIRMED,
            )
        elif days < 30:
            self.report(
                title="TLS certificate expiring within 30 days",
                severity=Severity.LOW,
                category="Transport Security",
                evidence=f"notAfter={not_after} ({days} days left)",
                remediation="Schedule/automate renewal.",
                confidence=Confidence.CONFIRMED,
            )

    def _check_protocols(self, host, port) -> None:
        for label, version in _LEGACY_PROTOCOLS:
            try:
                ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                ctx.minimum_version = version
                ctx.maximum_version = version
            except (ValueError, Exception):
                continue
            try:
                sock, ssock = self._connect(host, port, ctx, timeout=6)
                negotiated = ssock.version()
                ssock.close()
                sock.close()
                if negotiated:
                    self.report(
                        title=f"Obsolete TLS protocol supported: {negotiated}",
                        severity=Severity.MEDIUM,
                        category="Transport Security",
                        evidence=f"Server negotiated {negotiated}",
                        description="Deprecated TLS versions have known weaknesses (BEAST, "
                        "POODLE-style downgrades) and fail PCI/modern compliance.",
                        remediation="Disable TLS 1.0/1.1; require TLS 1.2+ (prefer 1.3).",
                        confidence=Confidence.CONFIRMED,
                    )
            except Exception:
                # Negotiation refused == good, protocol not supported.
                pass

    def _check_http_redirect(self) -> None:
        # Does plain HTTP redirect to HTTPS?
        http_url = f"http://{self.ctx.netloc}"
        r = self.http.get(http_url, allow_redirects=False, timeout=8)
        if r is None:
            return
        loc = r.headers.get("Location", "")
        if r.status_code in (301, 302, 303, 307, 308) and loc.startswith("https://"):
            return  # good
        if r.status_code == 200:
            self.report(
                title="HTTP does not redirect to HTTPS",
                severity=Severity.MEDIUM,
                category="Transport Security",
                url=http_url,
                evidence=f"HTTP {r.status_code}, no HTTPS redirect",
                description="The site answers over plaintext HTTP without forcing HTTPS, "
                "allowing downgrade and interception.",
                remediation="Redirect all HTTP traffic to HTTPS with a 301 and enable HSTS.",
                confidence=Confidence.FIRM,
            )
