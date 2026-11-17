import hashlib
import json
from pathlib import Path

import pytest

from bellplan.resources import builtin_media, decode_sources, encode_sources, resource_root


def test_builtin_media_catalogue_and_hashes():
    root = resource_root()
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    media = builtin_media()
    assert len(media) == manifest["media_count"]
    assert len({item["relative_path"] for item in media}) == manifest["media_count"]
    assert {item["relative_path"] for item in media} == {
        path.relative_to(root).as_posix() for path in (root / "music").rglob("*") if path.is_file()
    }
    hashes = {entry["relative_path"]: entry["sha256"] for entry in manifest["media"]}
    for item in media:
        path = Path(item["path"])
        assert path.is_absolute()
        assert path.is_relative_to(root.resolve())
        assert path.suffix.lower() in {".mp3", ".mp2"}
        assert path.stat().st_size == item["size"]
        assert isinstance(item["aliases"], list)
        assert hashlib.sha256(path.read_bytes()).hexdigest() == hashes[item["relative_path"]]


def test_builtin_media_categories_and_cwd_independence(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    media = builtin_media()
    manifest = json.loads((resource_root() / "manifest.json").read_text(encoding="utf-8"))
    assert {item["category"] for item in media} == {
        entry["category"] for entry in manifest["media"]
    }
    assert all(Path(item["path"]).is_file() for item in media)


def test_bundled_help_is_independently_written_static_document():
    root = resource_root()
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["help"] == []
    text = (root / "help" / "index.html").read_text(encoding="utf-8")
    assert "<html" in text.lower()
    assert "帮助" in text
    assert "<script" not in text.lower()
    assert "onclick=" not in text.lower()
    assert 'http-equiv="refresh"' not in text.lower()
    assert not any(path.suffix.lower() in {".dll", ".exe", ".ax"} for path in root.rglob("*"))


def test_builtin_sources_round_trip_and_preserve_custom_paths():
    media = builtin_media()
    paths = [item["path"] for item in media]
    uris = ["builtin://" + item["relative_path"] for item in media]
    custom = ["C:\\Music\\English #1.mp3", "/home/user/自定义.mp3", "relative/文件.mp3", ""]
    assert encode_sources(paths + custom) == uris + custom
    assert encode_sources(uris) == uris
    assert decode_sources(uris + custom) == paths + custom
    assert any("With An Orchid" in uri for uri in uris)
    assert any("考试指令" in uri for uri in uris)
    for before, after in zip(paths, decode_sources(uris)):
        assert (
            hashlib.sha256(Path(before).read_bytes()).digest()
            == hashlib.sha256(Path(after).read_bytes()).digest()
        )


@pytest.mark.parametrize(
    "uri",
    [
        "builtin://",
        "builtin://music/不存在.mp3",
        "builtin://music/../../outside.mp3",
        "builtin:///etc/passwd",
        "builtin://music/%2e%2e/outside.mp3",
        "builtin://music\\..\\outside.mp3",
        "builtin://help/index.html",
        "builtin://music/提示音a.mp3#fragment",
    ],
)
def test_unknown_and_traversal_builtin_sources_are_rejected(uri):
    with pytest.raises(ValueError, match="Unknown bundled media reference"):
        encode_sources([uri])
    with pytest.raises(ValueError, match="Unknown bundled media reference"):
        decode_sources([uri])


def test_encoding_requires_exact_absolute_path():
    item = builtin_media()[0]
    variant = str(Path(item["path"]).parent) + "/./" + Path(item["path"]).name
    assert encode_sources([variant, item["relative_path"]]) == [variant, item["relative_path"]]


def test_deduplicated_aliases_preserve_old_paths_and_uris():
    root = resource_root()
    media = builtin_media()
    canonical = next(
        item for item in media if item["relative_path"] == "music/打铃/上课-女童（17s）.mp3"
    )
    alias = "music/打铃/上课-人声（17s）.mp3"
    assert canonical["aliases"] == [alias]
    assert not (root / alias).exists()
    assert not any(item["relative_path"] == alias for item in media)
    alias_path = str((root / alias).resolve())
    alias_uri = "builtin://" + alias
    canonical_uri = "builtin://" + canonical["relative_path"]
    assert encode_sources([alias_path, alias_uri, canonical["path"]]) == [canonical_uri] * 3
    assert decode_sources([alias_uri, canonical_uri]) == [canonical["path"]] * 2


def test_missing_aliases_default_to_empty_lists():
    root = resource_root()
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    expected = {entry["relative_path"]: entry.get("aliases", []) for entry in manifest["media"]}
    assert any(not aliases for aliases in expected.values())
    for item in builtin_media():
        assert item["aliases"] == expected[item["relative_path"]]


def test_builtin_reference_survives_relocation(tmp_path, monkeypatch):
    import shutil

    import bellplan.resources as resources

    original_root = resource_root()
    manifest = json.loads((original_root / "manifest.json").read_text(encoding="utf-8"))
    entries = [
        entry
        for entry in manifest["media"]
        if "With An Orchid" in entry["name"] or entry["name"] == "提示音a"
    ]
    assert len(entries) == 2
    paths = [str((original_root / entry["relative_path"]).resolve()) for entry in entries]
    backup = encode_sources(paths)
    for entry in entries:
        target = tmp_path / entry["relative_path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original_root / entry["relative_path"], target)
    (tmp_path / "manifest.json").write_text(
        json.dumps({"media": entries}, ensure_ascii=False), encoding="utf-8"
    )
    monkeypatch.setattr(resources, "resource_root", lambda: tmp_path)
    relocated = decode_sources(backup)
    assert relocated != paths
    assert encode_sources(relocated) == backup
    for entry, path in zip(entries, relocated):
        assert Path(path).is_relative_to(tmp_path)
        assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == entry["sha256"]
