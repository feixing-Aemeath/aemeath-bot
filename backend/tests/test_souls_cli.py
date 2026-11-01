from core.bots import CHARACTERS
from scripts import souls


def test_import_names_preserve_existing_souls_and_replace(tmp_path, capsys):
    source, vault = tmp_path / "待导入", tmp_path / "vault"
    source.mkdir()
    for name in CHARACTERS.values():
        (source / f"{name}.soul.md").write_text(f"{name}的人设", encoding="utf-8")
    for filename in ("陌生人.soul.md", "成绩.md", "说明.txt"):
        (source / filename).write_text("不要导入", encoding="utf-8")
    args = ["--vault-dir", str(vault), "import", str(source)]
    assert souls.main(args) == 0
    printed = capsys.readouterr().out
    for name in CHARACTERS.values():
        assert f"已导入「{name}」" in printed
        assert (vault / "Bots" / name / "soul.md").read_text(encoding="utf-8") == f"{name}的人设"
    for filename in ("陌生人.soul.md", "成绩.md", "说明.txt"):
        assert f"跳过「{filename}」：认不出角色文件名" in printed
    assert not (vault / "Bots" / "陌生人").exists()

    (source / "成绩.soul.md").write_text("新的人设", encoding="utf-8")
    assert souls.main(args) == 0
    assert "已有 soul.md，用 --replace 才会覆盖" in capsys.readouterr().out
    path = vault / "Bots" / "成绩" / "soul.md"
    assert path.read_text(encoding="utf-8") == "成绩的人设"
    assert souls.main([*args, "--replace"]) == 0
    assert "已导入「成绩」" in capsys.readouterr().out
    assert path.read_text(encoding="utf-8") == "新的人设"


def test_list_reports_all_characters_and_byte_sizes_without_creating_files(tmp_path, capsys):
    vault = tmp_path / "vault"
    args = ["--vault-dir", str(vault), "list"]
    assert souls.main(args) == 0
    printed = capsys.readouterr().out
    for name in CHARACTERS.values():
        assert name in printed
    assert "还没有" in printed and not vault.exists()

    folder = vault / "Bots" / "作业"
    folder.mkdir(parents=True)
    (folder / "soul.md").write_text("人设", encoding="utf-8")
    (folder / "记忆.md").write_text("长期笔记", encoding="utf-8")
    assert souls.main(args) == 0
    line = next(line for line in capsys.readouterr().out.splitlines() if "作业" in line)
    assert "已有" in line and f"{len('长期笔记'.encode())} 字节" in line


def test_vault_location_uses_environment_without_needing_a_token(tmp_path, monkeypatch):
    monkeypatch.setattr(
        souls.Location, "model_config", {**souls.Location.model_config, "env_file": None}
    )
    monkeypatch.delenv("ASSISTANT_VAULT_DIR", raising=False)
    monkeypatch.delenv("ASSISTANT_TOKEN", raising=False)
    monkeypatch.setenv("ASSISTANT_DATA_DIR", str(tmp_path / "state"))
    assert souls.vault(None) == tmp_path / "state" / "vault"
    monkeypatch.setenv("ASSISTANT_VAULT_DIR", str(tmp_path / "notes"))
    assert souls.vault(None) == tmp_path / "notes"
    assert souls.vault(tmp_path / "explicit") == tmp_path / "explicit"


def test_import_errors_do_not_overwrite_soul(tmp_path, capsys):
    vault, source = tmp_path / "vault", tmp_path / "source"
    args = ["--vault-dir", str(vault), "import", str(source), "--replace"]
    assert souls.main(args) == 1
    assert "找不到人设文件夹" in capsys.readouterr().err
    source.mkdir()
    (source / "成绩.soul.md").write_bytes(b"\xff")
    path = vault / "Bots" / "成绩" / "soul.md"
    path.parent.mkdir(parents=True)
    path.write_text("旧人设", encoding="utf-8")
    assert souls.main(args) == 1
    assert "操作失败" in capsys.readouterr().err
    assert path.read_text(encoding="utf-8") == "旧人设"
