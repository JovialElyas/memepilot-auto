import pytest

from memepilot.config.settings import Mode, Settings, SettingsError
from memepilot.logging_utils import MASK, redact


def test_default_mode_is_paper() -> None:
    assert Settings.from_env({}).mode is Mode.PAPER


def test_live_mode_refused_before_phase7() -> None:
    with pytest.raises(SettingsError):
        Settings.from_env({"MEMEPILOT_MODE": "live"})


def test_invalid_wallet_refused() -> None:
    with pytest.raises(SettingsError):
        Settings.from_env({"GMGN_TRADING_WALLET": "not-a-wallet"})


def test_redact_pem_and_kv() -> None:
    pem = "-----BEGIN PRIVATE KEY-----\nMC4CAQAwBQYDK2VwBCIEI\n-----END PRIVATE KEY-----"
    escaped = "-----BEGIN PRIVATE KEY-----\\nMC4CAQAw\\n-----END PRIVATE KEY-----"
    text = f"a {pem} b {escaped} GMGN_API_KEY=abcd1234efgh X-APIKEY: zzzzzzzz"
    out = redact(text)
    assert "MC4CAQ" not in out
    assert "abcd1234efgh" not in out
    assert "zzzzzzzz" not in out
    assert out.count(MASK) == 4
