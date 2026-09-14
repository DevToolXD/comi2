"""
Registry of all check modules. Order matters only for readability of the live
output — recon/passive first, then active injection tests, then exposure.

To add a new check: create a Module subclass and append it to ALL_MODULES.
"""

from .recon import ReconModule
from .headers import SecurityHeadersModule
from .tls import TlsModule
from .ports import PortScanModule
from .http_methods import HttpMethodsModule
from .cors import CorsModule
from .sensitive_files import SensitiveFilesModule
from .sqli import SqlInjectionModule
from .xss import XssModule
from .traversal import TraversalModule
from .cmdi import CommandInjectionModule
from .open_redirect import OpenRedirectModule
from .csrf import CsrfModule

ALL_MODULES = [
    ReconModule,
    SecurityHeadersModule,
    TlsModule,
    PortScanModule,
    HttpMethodsModule,
    CorsModule,
    SensitiveFilesModule,
    SqlInjectionModule,
    XssModule,
    TraversalModule,
    CommandInjectionModule,
    OpenRedirectModule,
    CsrfModule,
]

MODULE_NAMES = [m.name for m in ALL_MODULES]

__all__ = ["ALL_MODULES", "MODULE_NAMES"]
