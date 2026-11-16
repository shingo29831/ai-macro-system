"""Tests for Pydantic data models and ConfigManager persistence."""

import json
import pytest
from pydantic import SecretStr, ValidationError

from models.data_types import AppConfig
from models.executable_macro import ExecutableMacro
from utils.config_manager import ConfigManager


def test_app_config_validators_accept_valid_and_reject_invalid():
    cfg = AppConfig(ai_mode="custom", custom_server_host="ai-srv.local", custom_server_port="8843")
    assert cfg.ai_mode == "custom"

    with pytest.raises(ValidationError):
        AppConfig(ai_mode="unknown_mode")

    with pytest.raises(ValidationError):
        AppConfig(llm_host="http://bad host!")

    with pytest.raises(ValidationError):
        AppConfig(llm_port="70000")

    with pytest.raises(ValidationError):
        AppConfig(cv_port="0")


def test_config_manager_load_falls_back_on_missing_or_corrupt(tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.json"
    monkeypatch.setattr(ConfigManager, "CONFIG_PATH", str(cfg_file))

    default_cfg = ConfigManager.load_config()
    assert default_cfg.ai_mode == "local"

    cfg_file.write_text("{broken json", encoding="utf-8")
    recovered = ConfigManager.load_config()
    assert recovered.ai_mode == "local"

    cfg_file.write_text(json.dumps({"ai_mode": "invalid"}), encoding="utf-8")
    recovered_invalid = ConfigManager.load_config()
    assert recovered_invalid.ai_mode == "local"


def test_config_manager_save_and_load_preserves_secret_str(tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.json"
    monkeypatch.setattr(ConfigManager, "CONFIG_PATH", str(cfg_file))

    cfg = AppConfig(
        ai_mode="cloud",
        generator_api_key=SecretStr("gen-secret-key"),
        judge_api_key=SecretStr("judge-secret-key"),
    )
    ConfigManager.save_config(cfg)

    reloaded = ConfigManager.load_config()
    assert reloaded.ai_mode == "cloud"
    assert reloaded.generator_api_key.get_secret_value() == "gen-secret-key"
    assert reloaded.judge_api_key.get_secret_value() == "judge-secret-key"


def test_config_manager_unwrap_secrets_handles_arbitrary_secret_fields():
    unwrapped = ConfigManager._unwrap_secrets({
        "plain": "ok",
        "custom_secret": SecretStr("top-secret"),
        "nested": {"token": SecretStr("nested-secret"), "items": [SecretStr("list-secret")]},
    })
    assert unwrapped == {
        "plain": "ok",
        "custom_secret": "top-secret",
        "nested": {"token": "nested-secret", "items": ["list-secret"]},
    }


def test_executable_macro_parses_all_command_variants():
    macro = ExecutableMacro(
        macro_id="wf_1",
        commands=[
            {"method": "activate_window", "args": {"window_title": "Demo", "x": 0, "y": 0, "width": 800, "height": 600}},
            {"method": "wait", "args": {"duration": 0.5}},
            {"method": "click", "args": {"x": 100, "y": 200}},
            {"method": "type_text", "args": {"text": "hello"}},
            {"method": "loop_start", "args": {"data_source": "variable", "items_variable": "items", "item_variable": "row"}},
            {"method": "excel_action", "args": {"action": "read_cell", "cell": "A1"}},
            {"method": "browser_action", "args": {"action": "open_url", "url": "https://example.com"}},
            {"method": "loop_end", "args": {}},
        ],
    )
    assert len(macro.commands) == 8
    assert macro.commands[4].args.data_source == "variable"
