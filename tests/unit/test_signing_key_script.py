import importlib.util
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "generate_signing_key.py"


@pytest.fixture()
def mod(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location("gsk", SCRIPT)
    assert spec and spec.loader
    m = importlib.util.module_from_spec(spec)
    sys.modules["gsk"] = m
    spec.loader.exec_module(m)
    cfg = tmp_path / ".config" / "gmgn"
    monkeypatch.setattr(m, "CONFIG_DIR", cfg)
    monkeypatch.setattr(m, "ENV_FILE", cfg / ".env")
    monkeypatch.setattr(m, "KEYPAIR_FILE", cfg / "keypair.pem")
    monkeypatch.setattr(m.getpass, "getpass", lambda _prompt: "gmgn_testkey_123456")
    return m


def _write_official_keypair(path: Path) -> str:
    """Reproduit le format de `gmgn-cli config` (src/commands/config.ts)."""
    k = Ed25519PrivateKey.generate()
    priv = k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                           serialization.NoEncryption()).decode()
    pub = k.public_key().public_bytes(serialization.Encoding.PEM,
                                      serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# Private Key\n{priv}\n# Public Key\n{pub}\n", encoding="utf-8")
    return priv.strip()


def test_set_api_key_from_official_keypair(mod, capsys: pytest.CaptureFixture[str]) -> None:  # type: ignore[no-untyped-def]
    priv = _write_official_keypair(mod.KEYPAIR_FILE)
    assert mod.set_api_key() == 0
    env = mod.ENV_FILE.read_text(encoding="utf-8")
    assert env.splitlines()[0] == "GMGN_API_KEY=gmgn_testkey_123456"
    line = next(ln for ln in env.splitlines() if ln.startswith("GMGN_PRIVATE_KEY="))
    assert line.split("=", 1)[1].strip('"').replace("\\n", "\n").strip() == priv
    out = capsys.readouterr().out
    assert "BEGIN PRIVATE KEY" not in out and "gmgn_testkey" not in out  # rien de secret affiché


def test_generate_then_set_api_key(mod, capsys: pytest.CaptureFixture[str]) -> None:  # type: ignore[no-untyped-def]
    assert mod.generate() == 0
    out = capsys.readouterr().out
    assert "BEGIN PUBLIC KEY" in out and "BEGIN PRIVATE KEY" not in out
    assert mod.set_api_key() == 0
    env = mod.ENV_FILE.read_text(encoding="utf-8")
    assert env.count("GMGN_PRIVATE_KEY=") == 1 and env.count("GMGN_API_KEY=") == 1


def test_set_api_key_without_any_key_fails(mod) -> None:  # type: ignore[no-untyped-def]
    assert mod.set_api_key() == 1
    assert not mod.ENV_FILE.exists()
