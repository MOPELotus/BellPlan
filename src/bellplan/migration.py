"""Extension point for independently implemented, explicitly registered importers."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol


class MigrationAdapter(Protocol):
    name: str

    def recognizes(self, source: Path) -> bool: ...

    def preview(self, source: Path) -> dict: ...

    def convert(self, source: Path) -> dict: ...


class MigrationRegistry:
    """No legacy binary formats are assumed or automatically executed."""

    def __init__(self):
        self._adapters: list[MigrationAdapter] = []

    def register(self, adapter: MigrationAdapter):
        if any(item.name == adapter.name for item in self._adapters):
            raise ValueError("迁移适配器名称重复")
        self._adapters.append(adapter)

    def preview(self, source: Path):
        source = Path(source)
        if not source.is_file():
            raise ValueError("迁移源文件不存在")
        for adapter in self._adapters:
            if adapter.recognizes(source):
                return {
                    "supported": True,
                    "adapter": adapter.name,
                    "preview": adapter.preview(source),
                }
        return {"supported": False, "message": "当前没有适用于此文件的迁移适配器"}
