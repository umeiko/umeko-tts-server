"""SQLite control plane. Raw API keys and session cookies are never persisted."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .errors import ApiError


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def quota_day() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


@dataclass(frozen=True)
class Principal:
    user_id: str
    name: str
    key_id: str | None
    priority: int
    max_concurrent: int
    max_queue: int
    rpm: int
    daily_chars: int
    max_text_chars: int

    @classmethod
    def admin(cls):
        return cls("admin", "管理员", None, 10, 1, 8, 120, 0, 4000)


class AccessStore:
    def __init__(self, data_dir: Path):
        data_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.db = sqlite3.connect(
            data_dir / "access.sqlite3",
            timeout=5,
            check_same_thread=False,
            isolation_level=None,
        )
        self.db.row_factory = sqlite3.Row
        if os.name != "nt":
            (data_dir / "access.sqlite3").chmod(0o600)
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA foreign_keys=ON;
            PRAGMA busy_timeout=5000;
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, enabled INTEGER NOT NULL,
                priority INTEGER NOT NULL, max_concurrent INTEGER NOT NULL, max_queue INTEGER NOT NULL,
                rpm INTEGER NOT NULL, daily_chars INTEGER NOT NULL, max_text_chars INTEGER NOT NULL,
                created REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS api_keys (
                id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), label TEXT NOT NULL,
                prefix TEXT NOT NULL, digest TEXT NOT NULL UNIQUE, created REAL NOT NULL,
                expires REAL, revoked INTEGER NOT NULL DEFAULT 0, last_used REAL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                digest TEXT PRIMARY KEY, version TEXT NOT NULL, expires REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS login_attempts (source TEXT NOT NULL, at REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS login_time ON login_attempts(at);
            CREATE TABLE IF NOT EXISTS usage (
                user_id TEXT NOT NULL, day TEXT NOT NULL, used INTEGER NOT NULL DEFAULT 0,
                reserved INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(user_id,day)
            );
            CREATE TABLE IF NOT EXISTS requests (
                id TEXT PRIMARY KEY, user_id TEXT NOT NULL, key_id TEXT, day TEXT NOT NULL,
                chars INTEGER NOT NULL, state TEXT NOT NULL, created REAL NOT NULL, finished REAL
            );
            CREATE INDEX IF NOT EXISTS request_rate ON requests(user_id,created);
            CREATE INDEX IF NOT EXISTS request_time ON requests(created);
            CREATE TABLE IF NOT EXISTS request_diagnostics (
                request_id TEXT PRIMARY KEY REFERENCES requests(id) ON DELETE CASCADE,
                voice TEXT NOT NULL DEFAULT '', started REAL,
                error TEXT NOT NULL DEFAULT '', error_type TEXT NOT NULL DEFAULT '',
                traceback TEXT NOT NULL DEFAULT '', progress TEXT NOT NULL DEFAULT '{}'
            );
            CREATE TABLE IF NOT EXISTS service_config (id INTEGER PRIMARY KEY, body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY, at REAL NOT NULL,
                action TEXT NOT NULL, target TEXT NOT NULL);
        """)

    @contextmanager
    def tx(self):
        with self._lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                yield self.db
                self.db.execute("COMMIT")
            except BaseException:
                self.db.execute("ROLLBACK")
                raise

    def close(self):
        self.db.close()

    @staticmethod
    def _audit(db, action: str, target: str):
        db.execute(
            "INSERT INTO audit(at,action,target) VALUES(?,?,?)",
            (time.time(), action, target),
        )
        db.execute(
            "DELETE FROM audit WHERE id < (SELECT COALESCE(MAX(id),0)-2000 FROM audit)"
        )

    def audit(self):
        with self.tx() as db:
            return [
                dict(r)
                for r in db.execute("SELECT * FROM audit ORDER BY id DESC LIMIT 50")
            ]

    def users(self):
        with self.tx() as db:
            rows = db.execute(
                """SELECT u.*, COALESCE(g.used,0) AS used_chars,
                COALESCE(g.reserved,0) AS reserved_chars FROM users u LEFT JOIN usage g
                ON g.user_id=u.id AND g.day=? ORDER BY u.created DESC""",
                (quota_day(),),
            ).fetchall()
            return [dict(r) for r in rows]

    def save_user(self, values: dict, user_id: str | None = None):
        with self.tx() as db:
            if (
                user_id
                and not db.execute(
                    "SELECT id FROM users WHERE id=?", (user_id,)
                ).fetchone()
            ):
                raise ApiError(404, "用户不存在")
            uid = user_id or uuid.uuid4().hex
            columns = (
                "name",
                "enabled",
                "priority",
                "max_concurrent",
                "max_queue",
                "rpm",
                "daily_chars",
                "max_text_chars",
            )
            try:
                if user_id:
                    db.execute(
                        "UPDATE users SET "
                        + ",".join(f"{c}=?" for c in columns)
                        + " WHERE id=?",
                        (*[values[c] for c in columns], uid),
                    )
                else:
                    db.execute(
                        "INSERT INTO users VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (uid, *[values[c] for c in columns], time.time()),
                    )
            except sqlite3.IntegrityError:
                raise ApiError(409, "用户名称已存在") from None
            self._audit(db, "user.update" if user_id else "user.create", uid)
            return dict(db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone())

    @staticmethod
    def _principal(row, key_id):
        return Principal(
            row["id"],
            row["name"],
            key_id,
            row["priority"],
            row["max_concurrent"],
            row["max_queue"],
            row["rpm"],
            row["daily_chars"],
            row["max_text_chars"],
        )

    def _policy(self, db, user_id: str, key_id: str | None):
        if user_id == "admin":
            return Principal.admin()
        user = db.execute(
            "SELECT * FROM users WHERE id=? AND enabled=1", (user_id,)
        ).fetchone()
        key = db.execute(
            "SELECT * FROM api_keys WHERE id=? AND user_id=? AND revoked=0",
            (key_id, user_id),
        ).fetchone()
        if (
            not user
            or not key
            or (key["expires"] is not None and key["expires"] <= time.time())
        ):
            raise ApiError(401, "用户或 API Key 已停用、过期")
        return self._principal(user, key_id)

    def policy(self, user_id: str, key_id: str | None):
        with self.tx() as db:
            return self._policy(db, user_id, key_id)

    def authenticate(self, raw: str):
        if not raw.startswith("utts_") or len(raw) > 128:
            raise ApiError(401, "API Key 无效或已过期")
        with self.tx() as db:
            row = db.execute(
                "SELECT id,user_id FROM api_keys WHERE digest=?", (digest(raw),)
            ).fetchone()
            if not row:
                raise ApiError(401, "API Key 无效或已过期")
            return self._policy(db, row["user_id"], row["id"])

    def keys(self):
        with self.tx() as db:
            return [
                dict(r)
                for r in db.execute("""SELECT k.id,k.user_id,u.name AS user_name,k.label,
                k.prefix,k.created,k.expires,k.revoked,k.last_used FROM api_keys k
                JOIN users u ON u.id=k.user_id ORDER BY k.created DESC""")
            ]

    @staticmethod
    def _insert_key(db, uid: str, label: str, expires: float | None):
        if not db.execute(
            "SELECT id FROM users WHERE id=? AND enabled=1", (uid,)
        ).fetchone():
            raise ApiError(400, "请先创建并启用用户")
        if (
            db.execute(
                "SELECT COUNT(*) FROM api_keys WHERE user_id=? AND revoked=0", (uid,)
            ).fetchone()[0]
            >= 20
        ):
            raise ApiError(400, "每名用户最多保留 20 个未撤销的密钥")
        raw, kid = "utts_" + secrets.token_urlsafe(32), uuid.uuid4().hex
        db.execute(
            "INSERT INTO api_keys(id,user_id,label,prefix,digest,created,expires) VALUES(?,?,?,?,?,?,?)",
            (kid, uid, label, raw[:13], digest(raw), time.time(), expires),
        )
        return {
            "id": kid,
            "user_id": uid,
            "key": raw,
            "prefix": raw[:13],
            "expires": expires,
        }

    def issue_key(self, uid: str, label: str, days: int):
        expires = time.time() + days * 86400 if days else None
        with self.tx() as db:
            result = self._insert_key(db, uid, label, expires)
            self._audit(db, "key.issue", result["id"])
            return result

    def revoke_key(self, key_id: str):
        with self.tx() as db:
            if not db.execute(
                "SELECT id FROM api_keys WHERE id=?", (key_id,)
            ).fetchone():
                raise ApiError(404, "密钥不存在")
            db.execute("UPDATE api_keys SET revoked=1 WHERE id=?", (key_id,))
            self._audit(db, "key.revoke", key_id)

    def rotate_key(self, key_id: str):
        with self.tx() as db:
            old = db.execute(
                "SELECT * FROM api_keys WHERE id=? AND revoked=0", (key_id,)
            ).fetchone()
            if not old:
                raise ApiError(404, "可轮换的密钥不存在")
            if old["expires"] is not None and old["expires"] <= time.time():
                raise ApiError(400, "已过期的密钥请重新发放")
            db.execute("UPDATE api_keys SET revoked=1 WHERE id=?", (key_id,))
            result = self._insert_key(db, old["user_id"], old["label"], old["expires"])
            self._audit(db, "key.rotate", key_id)
            return result

    def reserve(self, request_id: str, principal: Principal, chars: int, voice=""):
        now, day = time.time(), quota_day()
        with self.tx() as db:
            p = self._policy(db, principal.user_id, principal.key_id)
            if chars > p.max_text_chars:
                raise ApiError(400, f"单次文本最多 {p.max_text_chars} 字符")
            count = db.execute(
                "SELECT COUNT(*) FROM requests WHERE user_id=? AND created>?",
                (p.user_id, now - 60),
            ).fetchone()[0]
            if count >= p.rpm:
                raise ApiError(429, "用户每分钟请求数已达上限", {"Retry-After": "60"})
            db.execute(
                "INSERT OR IGNORE INTO usage(user_id,day) VALUES(?,?)", (p.user_id, day)
            )
            usage = db.execute(
                "SELECT used,reserved FROM usage WHERE user_id=? AND day=?",
                (p.user_id, day),
            ).fetchone()
            if (
                p.daily_chars
                and usage["used"] + usage["reserved"] + chars > p.daily_chars
            ):
                raise ApiError(
                    429, "今日字符额度不足（包含正在排队和合成的预留额度，UTC 日界线）"
                )
            db.execute(
                "UPDATE usage SET reserved=reserved+? WHERE user_id=? AND day=?",
                (chars, p.user_id, day),
            )
            db.execute(
                "INSERT INTO requests VALUES(?,?,?,?,?,?,?,NULL)",
                (request_id, p.user_id, p.key_id, day, chars, "pending", now),
            )
            db.execute(
                "INSERT INTO request_diagnostics(request_id,voice) VALUES(?,?)",
                (request_id, voice),
            )
            if p.key_id:
                db.execute(
                    "UPDATE api_keys SET last_used=? WHERE id=?", (now, p.key_id)
                )
            db.execute(
                "DELETE FROM requests WHERE created<? AND state!='pending'",
                (now - 30 * 86400,),
            )
            return p

    def finish(self, request_id: str, outcome: str, details=None):
        with self.tx() as db:
            row = db.execute(
                "SELECT * FROM requests WHERE id=? AND state='pending'", (request_id,)
            ).fetchone()
            if not row:
                return
            db.execute(
                "UPDATE usage SET reserved=MAX(0,reserved-?),used=used+? WHERE user_id=? AND day=?",
                (
                    row["chars"],
                    row["chars"] if outcome == "completed" else 0,
                    row["user_id"],
                    row["day"],
                ),
            )
            db.execute(
                "UPDATE requests SET state=?,finished=? WHERE id=?",
                (outcome, time.time(), request_id),
            )
            if details is not None:
                db.execute(
                    """INSERT INTO request_diagnostics
                    (request_id,voice,started,error,error_type,traceback,progress)
                    VALUES(?,?,?,?,?,?,?) ON CONFLICT(request_id) DO UPDATE SET
                    voice=excluded.voice,started=excluded.started,error=excluded.error,
                    error_type=excluded.error_type,traceback=excluded.traceback,
                    progress=excluded.progress""",
                    (
                        request_id,
                        details.get("voice", ""),
                        details.get("started"),
                        details.get("error", ""),
                        details.get("error_type", ""),
                        details.get("traceback", ""),
                        json.dumps(details.get("progress", {})),
                    ),
                )

    def recover(self):
        """Called only under the process lock: a restart interrupts in-memory jobs."""
        with self.tx() as db:
            db.execute(
                "UPDATE requests SET state='interrupted',finished=? WHERE state='pending'",
                (time.time(),),
            )
            db.execute("UPDATE usage SET reserved=0")
            db.execute("DELETE FROM sessions WHERE expires<?", (time.time(),))

    def request_history(self):
        with self.tx() as db:
            return [
                dict(r)
                for r in db.execute("""SELECT r.id,r.user_id,COALESCE(u.name,'管理员') AS user_name,
                r.key_id,r.chars,r.state,r.created,r.finished,d.voice,d.started,d.error,
                d.error_type,COALESCE(d.traceback!='',0) AS has_traceback
                FROM requests r LEFT JOIN users u ON u.id=r.user_id
                LEFT JOIN request_diagnostics d ON d.request_id=r.id
                ORDER BY r.created DESC LIMIT 50""")
            ]

    def request_detail(self, request_id: str):
        """Admin-only payload, loaded on demand instead of in every queue poll."""
        with self.tx() as db:
            row = db.execute(
                """SELECT r.id,r.user_id,COALESCE(u.name,'管理员') AS user_name,
                r.key_id,r.chars,r.state,r.created,r.finished,d.request_id AS diagnostic_id,
                d.voice,d.started,d.error,d.error_type,d.traceback,d.progress
                FROM requests r LEFT JOIN users u ON u.id=r.user_id
                LEFT JOIN request_diagnostics d ON d.request_id=r.id WHERE r.id=?""",
                (request_id,),
            ).fetchone()
            if row is None:
                raise ApiError(404, "调用记录不存在或已过保留期限")
            result = dict(row)
            result["diagnostics_available"] = result.pop("diagnostic_id") is not None
            result["progress"] = json.loads(result["progress"] or "{}")
            return result

    def load_config(self, defaults: dict):
        import json

        with self.tx() as db:
            row = db.execute("SELECT body FROM service_config WHERE id=1").fetchone()
            return {**defaults, **(json.loads(row[0]) if row else {})}

    def save_config(self, values: dict):
        import json

        with self.tx() as db:
            db.execute(
                "INSERT OR REPLACE INTO service_config VALUES(1,?)",
                (json.dumps(values),),
            )
            self._audit(db, "queue.configure", "scheduler")

    def login_attempt(self, source: str):
        now = time.time()
        source = digest(source)
        with self.tx() as db:
            db.execute("DELETE FROM login_attempts WHERE at<?", (now - 60,))
            count = db.execute(
                "SELECT COUNT(*) FROM login_attempts WHERE source=?", (source,)
            ).fetchone()[0]
            total = db.execute("SELECT COUNT(*) FROM login_attempts").fetchone()[0]
            if count >= 5 or total >= 100:
                raise ApiError(
                    429, "登录尝试过多，请一分钟后重试", {"Retry-After": "60"}
                )
            db.execute("INSERT INTO login_attempts VALUES(?,?)", (source, now))

    def create_session(self, version: str, seconds: int):
        raw = secrets.token_urlsafe(32)
        with self.tx() as db:
            db.execute("DELETE FROM sessions WHERE expires<?", (time.time(),))
            db.execute(
                "INSERT INTO sessions VALUES(?,?,?)",
                (digest(raw), version, time.time() + seconds),
            )
            db.execute(
                "DELETE FROM sessions WHERE digest NOT IN (SELECT digest FROM sessions ORDER BY expires DESC LIMIT 100)"
            )
        return raw

    def valid_session(self, raw: str, version: str):
        if not raw or len(raw) > 128:
            return False
        with self.tx() as db:
            return bool(
                db.execute(
                    "SELECT digest FROM sessions WHERE digest=? AND version=? AND expires>?",
                    (digest(raw), version, time.time()),
                ).fetchone()
            )

    def delete_session(self, raw: str):
        with self.tx() as db:
            db.execute("DELETE FROM sessions WHERE digest=?", (digest(raw),))
