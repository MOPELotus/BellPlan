"""Locate bundled media in source installs, wheels and PyInstaller bundles."""

from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
from typing import TypedDict


class BuiltinMedia(TypedDict):
    name: str
    category: str
    path: str
    relative_path: str
    size: int
    aliases: list[str]


def resource_root() -> Path:
    """PyInstaller preserves this package-relative location in its data bundle."""
    return Path(__file__).resolve().parent / "assets"


def builtin_media() -> list[BuiltinMedia]:
    """Return the installed catalogue without depending on the working directory."""
    root = resource_root()
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    result: list[BuiltinMedia] = []
    for entry in manifest["media"]:
        relative_path = entry["relative_path"]
        path = (root / relative_path).resolve()
        if not path.is_relative_to(root.resolve()):
            raise ValueError(f"Bundled media path escapes asset directory: {relative_path}")
        if not path.is_file():
            raise FileNotFoundError(f"Bundled media is missing: {relative_path}")
        aliases = list(entry.get("aliases", []))
        for alias in aliases:
            relative = PurePosixPath(alias)
            if (
                relative.is_absolute()
                or relative.as_posix() != alias
                or ".." in relative.parts
                or "\\" in alias
                or not alias.startswith("music/")
                or not (root / alias).resolve().is_relative_to(root.resolve())
            ):
                raise ValueError(f"Invalid bundled media alias: {alias}")
        result.append(
            {
                "name": entry["name"],
                "category": entry["category"],
                "path": str(path),
                "relative_path": relative_path,
                "size": entry["size"],
                "aliases": aliases,
            }
        )
    return result


def _source_maps() -> tuple[dict[str, str], dict[str, str]]:
    """Build exact, allowlisted URI mappings from the installed catalogue."""
    catalogue = builtin_media()
    by_path = {entry["path"]: "builtin://" + entry["relative_path"] for entry in catalogue}
    by_uri = {uri: path for path, uri in by_path.items()}
    root = resource_root()
    for entry in catalogue:
        canonical_uri = "builtin://" + entry["relative_path"]
        for alias in entry["aliases"]:
            alias_path = str((root / alias).resolve())
            alias_uri = "builtin://" + alias
            if alias_uri in by_uri and by_uri[alias_uri] != entry["path"]:
                raise ValueError(f"Conflicting bundled media alias: {alias}")
            if alias_path in by_path and by_path[alias_path] != canonical_uri:
                raise ValueError(f"Conflicting bundled media alias: {alias}")
            by_path[alias_path] = canonical_uri
            by_uri[alias_uri] = entry["path"]
    return by_path, by_uri


def encode_sources(paths: list[str]) -> list[str]:
    """Store bundled audio as portable references; leave user paths untouched."""
    by_path, by_uri = _source_maps()
    result: list[str] = []
    for path in paths:
        if path.startswith("builtin://"):
            if path not in by_uri:
                raise ValueError(f"Unknown bundled media reference: {path}")
            result.append(by_path[by_uri[path]])
        else:
            result.append(by_path.get(path, path))
    return result


def decode_sources(paths: list[str]) -> list[str]:
    """Resolve only exact manifest references, without interpreting URI paths."""
    _, by_uri = _source_maps()
    result: list[str] = []
    for path in paths:
        if path.startswith("builtin://"):
            if path not in by_uri:
                raise ValueError(f"Unknown bundled media reference: {path}")
            result.append(by_uri[path])
        else:
            result.append(path)
    return result
