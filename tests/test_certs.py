"""certs.py CA-bundle resolution tests.

resolve_ca_bundle() picks the most complete CA bundle for the environment:
an operator-set CA env var first, then the OS trust store, then certifi.
prime_ca_env() exports that path to the standard CA env vars without
clobbering an operator-provided value.
"""
import certs


def _clear_ca_env(monkeypatch):
    for var in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "HTTPLIB2_CA_CERTS"):
        monkeypatch.delenv(var, raising=False)


def test_resolve_respects_env_var(monkeypatch, tmp_path):
    _clear_ca_env(monkeypatch)
    bundle = tmp_path / "corp-ca.pem"
    bundle.write_text("cert", encoding="utf-8")
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", str(bundle))
    assert certs.resolve_ca_bundle() == str(bundle)


def test_resolve_ignores_env_var_pointing_at_missing_file(monkeypatch, tmp_path):
    _clear_ca_env(monkeypatch)
    monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "does-not-exist.pem"))
    # No system bundle in this test → falls through to certifi (a real file).
    monkeypatch.setattr(certs, "_SYSTEM_BUNDLES", ())
    result = certs.resolve_ca_bundle()
    import certifi

    assert result == certifi.where()


def test_resolve_prefers_system_bundle_over_certifi(monkeypatch, tmp_path):
    _clear_ca_env(monkeypatch)
    sys_bundle = tmp_path / "ca-certificates.crt"
    sys_bundle.write_text("cert", encoding="utf-8")
    monkeypatch.setattr(certs, "_SYSTEM_BUNDLES", (str(sys_bundle),))
    assert certs.resolve_ca_bundle() == str(sys_bundle)


def test_resolve_falls_back_to_certifi(monkeypatch):
    _clear_ca_env(monkeypatch)
    monkeypatch.setattr(certs, "_SYSTEM_BUNDLES", ("/no/such/bundle.crt",))
    import certifi

    assert certs.resolve_ca_bundle() == certifi.where()


def test_prime_sets_all_env_vars(monkeypatch, tmp_path):
    _clear_ca_env(monkeypatch)
    sys_bundle = tmp_path / "ca-certificates.crt"
    sys_bundle.write_text("cert", encoding="utf-8")
    monkeypatch.setattr(certs, "_SYSTEM_BUNDLES", (str(sys_bundle),))
    primed = certs.prime_ca_env()
    assert primed == str(sys_bundle)
    import os

    for var in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "HTTPLIB2_CA_CERTS"):
        assert os.environ[var] == str(sys_bundle)


def test_prime_does_not_clobber_operator_value(monkeypatch, tmp_path):
    _clear_ca_env(monkeypatch)
    operator = tmp_path / "operator.pem"
    operator.write_text("cert", encoding="utf-8")
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", str(operator))
    certs.prime_ca_env()
    import os

    # The pre-set value is preserved (setdefault); others get filled in.
    assert os.environ["REQUESTS_CA_BUNDLE"] == str(operator)
