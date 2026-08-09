"""설정 로드 테스트 — 기본값, 파일 덮어쓰기, 환경변수, 잘못된 입력."""

from __future__ import annotations

import json

import pytest

from afterlimit.config import Config


def test_설정_없이도_기본값으로_동작한다(tmp_path):
    cfg = Config.load(tmp_path / "없음.json")
    assert cfg.max_resume_per_cycle == 2
    assert cfg.webhook_url is None
    assert cfg.claude_bin == "claude"


def test_파일이_기본값을_덮어쓴다(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"max_resume_per_cycle": 3, "claude_bin": "/opt/claude"}))
    cfg = Config.load(p)
    assert cfg.max_resume_per_cycle == 3
    assert cfg.claude_bin == "/opt/claude"
    assert cfg.resume_cooldown_hours == 5  # 안 건드린 값은 기본값 유지


def test_경로는_확장된다(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"projects_dir": "~/custom/projects"}))
    cfg = Config.load(p)
    assert "~" not in str(cfg.projects_dir)  # 홈이 펼쳐짐


def test_알_수_없는_항목은_거부한다(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"typo_field": 1}))
    with pytest.raises(ValueError, match="typo_field"):
        Config.load(p)


def test_깨진_json_은_명확히_실패한다(tmp_path):
    p = tmp_path / "config.json"
    p.write_text("{not json")
    with pytest.raises(ValueError, match="읽을 수 없"):
        Config.load(p)


def test_환경변수가_파일보다_우선한다(tmp_path, monkeypatch):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"webhook_url": "https://from-file"}))
    monkeypatch.setenv("AFTERLIMIT_WEBHOOK_URL", "https://from-env")
    cfg = Config.load(p)
    assert cfg.webhook_url == "https://from-env"


def test_dry_run_환경변수(tmp_path, monkeypatch):
    monkeypatch.setenv("AFTERLIMIT_DRY_RUN", "1")
    cfg = Config.load(tmp_path / "없음.json")
    assert cfg.dry_run is True


def test_파생_경로(tmp_path):
    cfg = Config(state_dir=tmp_path / "state")
    assert cfg.lock_dir == tmp_path / "state" / "locks"
    assert cfg.state_file == tmp_path / "state" / "state.json"
    assert cfg.log_file.parent == tmp_path / "state"
