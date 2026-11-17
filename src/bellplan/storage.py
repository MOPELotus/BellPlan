"""Transactional SQLite storage. User records have no artificial count limit."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from .domain import validate_plan, validate_program, validate_setting


class Store:
    def __init__(self, path, *, timeout=10):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(path), timeout=timeout)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS plans(id TEXT PRIMARY KEY, body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS programs(
                id TEXT PRIMARY KEY,
                plan_id TEXT NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
                body TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS programs_plan ON programs(plan_id);
            CREATE TABLE IF NOT EXISTS runs(
                event_id TEXT PRIMARY KEY, program_id TEXT NOT NULL,
                scheduled_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'claimed',
                message TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            INSERT OR IGNORE INTO metadata(key, value) VALUES('schema_version', '1');
        """)
        self.connection.commit()

    @contextmanager
    def transaction(self):
        with self.connection:
            yield

    def close(self):
        self.connection.close()

    def list_plans(self):
        return [
            json.loads(x["body"])
            for x in self.connection.execute("SELECT body FROM plans ORDER BY rowid")
        ]

    def save_plan(self, record):
        result = validate_plan(record)
        with self.connection:
            self.connection.execute(
                "INSERT INTO plans(id,body) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body",
                (result["id"], json.dumps(result, ensure_ascii=False)),
            )
        return result

    def delete_plan(self, identifier):
        with self.connection:
            self.connection.execute("DELETE FROM plans WHERE id=?", (identifier,))

    def list_programs(self, plan_id=None):
        if plan_id is None:
            rows = self.connection.execute("SELECT body FROM programs ORDER BY rowid")
        else:
            rows = self.connection.execute(
                "SELECT body FROM programs WHERE plan_id=? ORDER BY rowid", (plan_id,)
            )
        # Python's stable sort preserves SQLite rowid order for equal ranks and
        # avoids requiring SQLite's optional JSON extension on older systems.
        records = [json.loads(x["body"]) for x in rows]
        records.sort(key=lambda item: item.get("sort_order", 0))
        return records

    def move_program(self, identifier, delta):
        """Move one position within the same plan in one atomic rewrite."""
        if type(delta) is not int or delta not in (-1, 1):
            raise ValueError("移动方向必须是 -1 或 1")
        with self.connection:
            current = self.get_program(identifier)
            if current is None:
                raise ValueError("节目不存在")
            records = self.list_programs(current["plan_id"])
            index = next(i for i, record in enumerate(records) if record["id"] == identifier)
            target = index + delta
            if target < 0 or target >= len(records):
                return current
            records[index], records[target] = records[target], records[index]
            for position, record in enumerate(records):
                record["sort_order"] = position
                self.connection.execute(
                    "UPDATE programs SET body=? WHERE id=?",
                    (json.dumps(record, ensure_ascii=False), record["id"]),
                )
        return self.get_program(identifier)

    def get_program(self, identifier):
        row = self.connection.execute(
            "SELECT body FROM programs WHERE id=?", (identifier,)
        ).fetchone()
        return json.loads(row["body"]) if row else None

    def save_program(self, record):
        result = validate_program(record)
        with self.connection:
            try:
                self.connection.execute(
                    "INSERT INTO programs(id,plan_id,body) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET plan_id=excluded.plan_id,body=excluded.body",
                    (result["id"], result["plan_id"], json.dumps(result, ensure_ascii=False)),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("所属计划不存在") from exc
        return result

    def delete_program(self, identifier):
        with self.connection:
            self.connection.execute("DELETE FROM programs WHERE id=?", (identifier,))

    def duplicate_program(self, identifier):
        original = self.get_program(identifier)
        if original is None:
            raise ValueError("节目不存在")
        original.update(id=str(uuid4()), name=original["name"] + "（副本）")
        return self.save_program(original)

    def export_data(self):
        settings = {
            row["key"][8:]: json.loads(row["value"])
            for row in self.connection.execute(
                "SELECT key,value FROM metadata WHERE key LIKE 'setting:%'"
            )
        }
        return {
            "format": "bellplan",
            "version": 1,
            "plans": self.list_plans(),
            "programs": self.list_programs(),
            "settings": settings,
        }

    def import_data(self, data, replace=False):
        """Validate all rows before touching storage; import is one transaction.

        Runs survive replacement so re-importing a backup cannot replay an occurrence.
        """
        if (
            not isinstance(data, dict)
            or data.get("format") != "bellplan"
            or data.get("version") != 1
        ):
            raise ValueError("不支持的备份格式或版本")
        if not isinstance(data.get("plans"), list) or not isinstance(data.get("programs"), list):
            raise ValueError("备份缺少计划或节目列表")
        settings = data.get("settings", {})
        if not isinstance(settings, dict) or any(not isinstance(key, str) for key in settings):
            raise ValueError("备份设置必须是对象")
        encoded_settings = {
            key: json.dumps(validate_setting(key, value), ensure_ascii=False, allow_nan=False)
            for key, value in settings.items()
        }
        plans = [validate_plan(x) for x in data["plans"]]
        programs = [validate_program(x) for x in data["programs"]]
        if len({x["id"] for x in plans}) != len(plans) or len({x["id"] for x in programs}) != len(
            programs
        ):
            raise ValueError("备份中存在重复 ID")
        known = {p["id"] for p in plans}
        if not replace:
            known.update(p["id"] for p in self.list_plans())
        if any(x["plan_id"] not in known for x in programs):
            raise ValueError("备份中存在无所属计划的节目")
        with self.connection:
            if replace:
                self.connection.execute("DELETE FROM plans")
                self.connection.execute("DELETE FROM metadata WHERE key LIKE 'setting:%'")
            for item in plans:
                self.connection.execute(
                    "INSERT INTO plans(id,body) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body",
                    (item["id"], json.dumps(item, ensure_ascii=False)),
                )
            for item in programs:
                self.connection.execute(
                    "INSERT INTO programs(id,plan_id,body) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET plan_id=excluded.plan_id,body=excluded.body",
                    (item["id"], item["plan_id"], json.dumps(item, ensure_ascii=False)),
                )
            for key, value in encoded_settings.items():
                self.connection.execute(
                    "INSERT INTO metadata(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    ("setting:" + key, value),
                )
        return {"plans": len(plans), "programs": len(programs)}

    def claim(self, event_id, program_id, scheduled_at):
        with self.connection:
            cursor = self.connection.execute(
                "INSERT OR IGNORE INTO runs(event_id,program_id,scheduled_at) VALUES(?,?,?)",
                (event_id, program_id, scheduled_at),
            )
        return cursor.rowcount == 1

    def finish_run(self, event_id, status="completed", message=""):
        if status not in ("started", "completed", "failed", "cancelled"):
            raise ValueError("不支持的执行状态")
        with self.connection:
            self.connection.execute(
                "UPDATE runs SET status=?,message=? WHERE event_id=?", (status, message, event_id)
            )

    def list_runs(self, limit=200):
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise ValueError("日志条数必须是正整数")
        return [
            dict(x)
            for x in self.connection.execute(
                "SELECT * FROM runs ORDER BY rowid DESC LIMIT ?", (limit,)
            )
        ]

    def get_setting(self, key, default=None):
        row = self.connection.execute(
            "SELECT value FROM metadata WHERE key=?", ("setting:" + key,)
        ).fetchone()
        return json.loads(row["value"]) if row else default

    def set_setting(self, key, value):
        encoded = json.dumps(validate_setting(key, value), ensure_ascii=False, allow_nan=False)
        with self.connection:
            self.connection.execute(
                "INSERT INTO metadata(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                ("setting:" + key, encoded),
            )
