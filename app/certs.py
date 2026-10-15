"""certs.py — cross-platform CA bundle resolution for TLS-intercepting proxies.

Corporate and cloud egress proxies (the kind that sit in front of Anthropic's
managed routine environment) re-sign outbound TLS with a private CA. That CA is
installed in the operating system trust store but NOT in certifi's bundle, which
is what Python's `requests`, `httplib2`, `msal`, and `urllib` use by default.
The symptom is `SSL: CERTIFICATE_VERIFY_FAILED — self-signed certificate in
certificate chain` on every Google / Microsoft API call.

`resolve_ca_bundle()` picks the most complete bundle available (OS trust store
first, certifi last). `prime_ca_env()` exports the standard CA environment
variables so every HTTP library in the process trusts the same bundle. Both are
no-ops on a normal laptop (no system bundle file → certifi, same as the default),
so they are safe to call unconditionally at import time.
"""

import os
from pathlib import Path

# OS trust stores that include any locally-installed proxy CA. Ordered by how
# common each layout is. macOS/Windows have no file-based bundle here (they use
# Keychain / the Windows cert store) and fall through to certifi — which is fine,
# because those are the environments without an intercepting proxy.
_SYSTEM_BUNDLES = (
    "/etc/ssl/certs/ca-certificates.crt",   # Debian, Ubuntu
    "/etc/pki/tls/certs/ca-bundle.crt",     # RHEL, CentOS, Fedora
    "/etc/ssl/cert.pem",                    # Alpine, BSD, some macOS layouts
)

# Env vars the major HTTP libraries honour for a custom CA bundle.
_CA_ENV_VARS = ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "HTTPLIB2_CA_CERTS")


def resolve_ca_bundle() -> str:
    """Return the path to the most complete CA bundle for this environment.

    Resolution order:
      1. An operator-provided bundle already named in a CA env var (respected).
      2. The OS trust store, if present (this is where a proxy CA lives).
      3. certifi's bundle (the default everywhere else).
    """
    for var in _CA_ENV_VARS:
        val = os.environ.get(var)
        if val and Path(val).is_file():
            return val
    for path in _SYSTEM_BUNDLES:
        if Path(path).is_file():
            return path
    import certifi

    return certifi.where()


def prime_ca_env() -> str:
    """Point every HTTP library at the resolved bundle via the standard env vars.

    Uses setdefault, so an operator-provided value always wins and the call is
    idempotent. Returns the resolved bundle path. Safe on all platforms.
    """
    bundle = resolve_ca_bundle()
    for var in _CA_ENV_VARS:
        os.environ.setdefault(var, bundle)
    return bundle
