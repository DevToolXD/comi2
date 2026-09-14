"""
TCP port scan of the target host + banner grab. Focuses on services that
should almost never be exposed to the internet (databases, caches, admin
services) — the "server permission / exposure" surface the user cares about.
"""

from __future__ import annotations

import socket

from .base import Module, Severity, Confidence


# port -> (service, severity-if-open-to-internet, note)
_PORTS = {
    21: ("FTP", Severity.MEDIUM, "Plaintext file transfer; check for anonymous login."),
    22: ("SSH", Severity.LOW, "Admin access; ensure key-only auth and fail2ban."),
    23: ("Telnet", Severity.HIGH, "Plaintext remote shell — must never be exposed."),
    25: ("SMTP", Severity.LOW, "Mail; check for open relay."),
    53: ("DNS", Severity.LOW, "Check for zone transfer / open resolver."),
    110: ("POP3", Severity.LOW, "Plaintext mail retrieval."),
    111: ("rpcbind", Severity.MEDIUM, "RPC portmapper exposure."),
    135: ("MSRPC", Severity.MEDIUM, "Windows RPC exposure."),
    139: ("NetBIOS", Severity.MEDIUM, "SMB/NetBIOS exposure."),
    143: ("IMAP", Severity.LOW, "Plaintext mail."),
    389: ("LDAP", Severity.MEDIUM, "Directory service exposure."),
    443: ("HTTPS", Severity.INFO, "Expected for web."),
    445: ("SMB", Severity.HIGH, "SMB file sharing — frequent ransomware vector."),
    465: ("SMTPS", Severity.INFO, "Mail over TLS."),
    587: ("SMTP submission", Severity.INFO, "Mail submission."),
    993: ("IMAPS", Severity.INFO, "IMAP over TLS."),
    995: ("POP3S", Severity.INFO, "POP3 over TLS."),
    1433: ("MSSQL", Severity.HIGH, "Database should not face the internet."),
    1521: ("Oracle DB", Severity.HIGH, "Database should not face the internet."),
    2049: ("NFS", Severity.HIGH, "Network file system exposure."),
    2375: ("Docker API (plain)", Severity.CRITICAL, "Unauthenticated Docker API = full host RCE."),
    2376: ("Docker API (TLS)", Severity.HIGH, "Docker API exposure."),
    3306: ("MySQL/MariaDB", Severity.HIGH, "Database should not face the internet."),
    3389: ("RDP", Severity.HIGH, "Remote Desktop — common brute-force target."),
    4444: ("Metasploit/backdoor?", Severity.HIGH, "Unusual; often a backdoor/handler."),
    5432: ("PostgreSQL", Severity.HIGH, "Database should not face the internet."),
    5601: ("Kibana", Severity.HIGH, "Log UI often unauthenticated."),
    5900: ("VNC", Severity.HIGH, "Remote desktop; often weak/no auth."),
    5984: ("CouchDB", Severity.HIGH, "Database exposure."),
    6379: ("Redis", Severity.CRITICAL, "Redis usually has no auth → data/RCE."),
    7001: ("WebLogic", Severity.HIGH, "App server admin, many CVEs."),
    8000: ("HTTP-alt", Severity.LOW, "Dev/alt web service."),
    8080: ("HTTP-proxy/alt", Severity.LOW, "Alt web / proxy service."),
    8081: ("HTTP-alt", Severity.LOW, "Alt web service."),
    8443: ("HTTPS-alt", Severity.LOW, "Alt HTTPS service."),
    8888: ("HTTP-alt/Jupyter", Severity.MEDIUM, "Notebooks/admin often unauthenticated."),
    9200: ("Elasticsearch", Severity.CRITICAL, "ES usually unauthenticated → full data read/write."),
    9300: ("Elasticsearch transport", Severity.HIGH, "ES cluster transport exposure."),
    11211: ("Memcached", Severity.HIGH, "No auth; data exposure + amplification."),
    15672: ("RabbitMQ mgmt", Severity.HIGH, "Broker admin UI."),
    27017: ("MongoDB", Severity.CRITICAL, "MongoDB often unauthenticated → full data access."),
    27018: ("MongoDB shard", Severity.HIGH, "MongoDB exposure."),
    3000: ("Dev server/Grafana", Severity.MEDIUM, "Dev app / Grafana admin."),
    5000: ("Dev server", Severity.LOW, "Dev app service."),
}


class PortScanModule(Module):
    name = "ports"
    description = "Exposed TCP services / dangerous open ports"

    def run(self) -> None:
        host = self.ctx.host
        ip = self.ctx.ip or host
        if not host:
            return
        if not self.config.get("port_scan", True):
            self.log.status("ports: disabled via config")
            return

        ports = sorted(_PORTS.keys())
        extra = self.config.get("extra_ports") or []
        for p in extra:
            if p not in _PORTS:
                _PORTS[p] = ("custom", Severity.LOW, "User-specified port.")
                ports.append(p)

        self.log.status(f"Port scan: {len(ports)} ports on {ip}")
        open_ports = self.parallel(self._probe, ports, workers=min(64, len(ports)))

        if not open_ports:
            self.log.status("ports: no listening TCP ports found in the probed set")
            return

        for port, banner in sorted(open_ports):
            service, sev, note = _PORTS.get(port, ("unknown", Severity.LOW, ""))
            # Expected web ports on a web target are informational.
            if port in (80, 443) or (port == self.ctx.port):
                sev = Severity.INFO
            evidence = f"port {port}/tcp open ({service})"
            if banner:
                evidence += f" banner={banner!r}"
            self.report(
                title=f"Open port {port} ({service})",
                severity=sev,
                category="Network Exposure",
                url=f"{self.ctx.host}:{port}",
                evidence=evidence,
                description=note,
                remediation="If this service does not need to be public, bind it to localhost "
                "or restrict it with a firewall / security group.",
                confidence=Confidence.CONFIRMED,
            )

    def _probe(self, port):
        ip = self.ctx.ip or self.ctx.host
        try:
            with socket.create_connection((ip, port), timeout=self.config.get("port_timeout", 2.0)) as s:
                banner = ""
                try:
                    s.settimeout(1.5)
                    data = s.recv(128)
                    banner = data.decode("latin-1", errors="replace").strip()
                except Exception:
                    banner = ""
                return (port, banner)
        except Exception:
            return None
