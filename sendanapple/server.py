#!/usr/bin/env python3
"""送 WAGZAO 一部 iPhone Duo —— 共享账本 + 人工核验服务。

只依赖 Python 标准库。启动后：
- 主页面：/index.html 或 /
- 核验台：/admin
- API：/api/state、/api/claims、/api/admin/claims、/api/admin/approve、/api/admin/reject
"""
from __future__ import annotations

import argparse
import hmac
import json
import mimetypes
import os
import re
import secrets
import socket
import sqlite3
import threading
import time
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "fund.db"
ADMIN_KEY_FILE = BASE_DIR / "ADMIN_KEY.txt"
MAX_BODY_BYTES = 24 * 1024
CLAIM_RATE_SECONDS = 60
CLAIM_RATE_LIMIT = 6
COLORS = ["#f4d35e", "#c8d6b9", "#f0b39f", "#c9d8e8", "#e5c3a7", "#d8c9e9"]

mimetypes.add_type("image/webp", ".webp")


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def load_admin_key() -> str:
    env_key = os.environ.get("WAGZAO_ADMIN_KEY", "").strip()
    if env_key:
        return env_key
    if ADMIN_KEY_FILE.exists():
        key = ADMIN_KEY_FILE.read_text(encoding="utf-8").strip()
        if key:
            return key
    key = secrets.token_urlsafe(24)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ADMIN_KEY_FILE.write_text(key + "\n", encoding="utf-8")
    try:
        os.chmod(ADMIN_KEY_FILE, 0o600)
    except OSError:
        pass
    return key


ADMIN_KEY = load_admin_key()


def connect_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = connect_db()
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS claims (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                contact TEXT NOT NULL DEFAULT '',
                amount REAL NOT NULL,
                message TEXT NOT NULL DEFAULT '',
                target TEXT NOT NULL DEFAULT '',
                anonymous INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'pending'
                    CHECK(status IN ('pending', 'verified', 'rejected')),
                created_at TEXT NOT NULL,
                reviewed_at TEXT
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_claims_status ON claims(status, created_at)")
        conn.commit()
    finally:
        conn.close()


def clean_text(value, max_len: int, default: str = "") -> str:
    if not isinstance(value, str):
        return default
    value = re.sub(r"[\x00-\x1f\x7f]+", " ", value).strip()
    return value[:max_len]


def display_date(value: str) -> str:
    if not value:
        return "刚刚"
    try:
        dt = datetime.fromisoformat(value)
        if dt.date() == datetime.now().astimezone().date():
            return "今天"
        return dt.strftime("%m.%d")
    except (TypeError, ValueError):
        return "刚刚"


def public_claim(row: sqlite3.Row) -> dict:
    anonymous = bool(row["anonymous"])
    name = "一位不愿署名的朋友" if anonymous else row["name"]
    return {
        "id": row["id"],
        "name": name,
        "amount": round(float(row["amount"]), 2),
        "message": row["message"] or "悄悄支持了一下。",
        "dateLabel": display_date(row["reviewed_at"] or row["created_at"]),
        "initial": "匿" if anonymous else (name[:1] or "友"),
        "color": COLORS[int(row["id"]) % len(COLORS)],
    }


def admin_claim(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "code": row["code"],
        "name": row["name"],
        "contact": row["contact"],
        "amount": round(float(row["amount"]), 2),
        "message": row["message"],
        "target": row["target"],
        "anonymous": bool(row["anonymous"]),
        "status": row["status"],
        "createdAt": row["created_at"],
        "reviewedAt": row["reviewed_at"],
    }


def public_state() -> dict:
    conn = connect_db()
    try:
        rows = conn.execute(
            "SELECT * FROM claims WHERE status = 'verified' ORDER BY reviewed_at DESC, id DESC"
        ).fetchall()
    finally:
        conn.close()
    contributions = [public_claim(row) for row in rows]
    return {
        "ok": True,
        "contributions": contributions,
        "total": round(sum(item["amount"] for item in contributions), 2),
        "count": len(contributions),
        "updatedAt": now_iso(),
    }


def admin_state() -> dict:
    conn = connect_db()
    try:
        pending = conn.execute(
            "SELECT * FROM claims WHERE status = 'pending' ORDER BY created_at ASC, id ASC"
        ).fetchall()
        verified = conn.execute(
            "SELECT * FROM claims WHERE status = 'verified' ORDER BY reviewed_at DESC, id DESC LIMIT 100"
        ).fetchall()
        rejected = conn.execute(
            "SELECT * FROM claims WHERE status = 'rejected' ORDER BY reviewed_at DESC, id DESC LIMIT 40"
        ).fetchall()
    finally:
        conn.close()
    return {
        "ok": True,
        "pending": [admin_claim(row) for row in pending],
        "verified": [admin_claim(row) for row in verified],
        "rejected": [admin_claim(row) for row in rejected],
        "stats": {
            "pendingCount": len(pending),
            "verifiedCount": len(verified),
            "verifiedTotal": round(sum(float(row["amount"]) for row in verified), 2),
            "updatedAt": now_iso(),
        },
    }


rate_lock = threading.Lock()
claim_attempts: dict[str, list[float]] = {}


def allow_claim(ip: str) -> bool:
    now = time.time()
    with rate_lock:
        current = [t for t in claim_attempts.get(ip, []) if now - t < CLAIM_RATE_SECONDS]
        if len(current) >= CLAIM_RATE_LIMIT:
            claim_attempts[ip] = current
            return False
        current.append(now)
        claim_attempts[ip] = current
        if len(claim_attempts) > 1000:
            for key in list(claim_attempts.keys())[:200]:
                if not claim_attempts[key]:
                    claim_attempts.pop(key, None)
        return True


class GiftHandler(SimpleHTTPRequestHandler):
    server_version = "WagzaoGift/1.0"
    protocol_version = "HTTP/1.1"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(BASE_DIR), **kwargs)

    def log_message(self, fmt, *args):
        print("[%s] %s" % (self.log_date_time_string(), fmt % args))

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header("Referrer-Policy", "same-origin")
        super().end_headers()

    def send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def read_json(self) -> dict:
        raw_length = self.headers.get("Content-Length", "0")
        try:
            length = int(raw_length)
        except ValueError:
            raise ApiError(400, "请求长度不合法")
        if length <= 0:
            raise ApiError(400, "请求内容为空")
        if length > MAX_BODY_BYTES:
            raise ApiError(413, "请求内容太大")
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ApiError(400, "请求格式不是合法 JSON")
        if not isinstance(data, dict):
            raise ApiError(400, "请求格式不正确")
        return data

    def require_admin(self) -> None:
        supplied = self.headers.get("X-Admin-Key", "")
        if not hmac.compare_digest(supplied, ADMIN_KEY):
            time.sleep(0.15)
            raise ApiError(401, "管理员密钥不正确")

    def is_private_path(self, path: str) -> bool:
        if path.startswith("/data/") or path == "/data":
            return True
        if path in ("/ADMIN_KEY.txt", "/server.py", "/.DS_Store"):
            return True
        return False

    def do_GET(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        try:
            if path == "/api/health":
                self.send_json(200, {"ok": True, "service": "wagzao-iphone-duo", "time": now_iso()})
                return
            if path == "/api/state":
                self.send_json(200, public_state())
                return
            if path == "/api/admin/claims":
                self.require_admin()
                self.send_json(200, admin_state())
                return
            if path.startswith("/api/"):
                self.send_json(404, {"ok": False, "error": "接口不存在"})
                return
            if self.is_private_path(path):
                self.send_error(404)
                return
            if path == "/admin":
                self.path = "/admin.html"
            super().do_GET()
        except ApiError as err:
            try:
                self.send_json(err.status, {"ok": False, "error": err.message})
            except (BrokenPipeError, ConnectionResetError):
                pass
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as err:  # noqa: BLE001 - keep the local server alive
            print("GET error:", repr(err))
            try:
                self.send_json(500, {"ok": False, "error": "服务器内部错误"})
            except Exception:
                pass

    def do_POST(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        try:
            if path == "/api/claims":
                self.handle_claim()
                return
            if path == "/api/admin/approve":
                self.require_admin()
                self.handle_review("verified")
                return
            if path == "/api/admin/reject":
                self.require_admin()
                self.handle_review("rejected")
                return
            self.send_json(404, {"ok": False, "error": "接口不存在"})
        except ApiError as err:
            try:
                self.send_json(err.status, {"ok": False, "error": err.message})
            except (BrokenPipeError, ConnectionResetError):
                pass
        except Exception as err:  # noqa: BLE001
            print("POST error:", repr(err))
            try:
                self.send_json(500, {"ok": False, "error": "服务器内部错误"})
            except Exception:
                pass

    def handle_claim(self):
        ip = self.client_address[0]
        if not allow_claim(ip):
            raise ApiError(429, "提交太频繁，请过一分钟再试")
        data = self.read_json()
        name = clean_text(data.get("name"), 20, "一位朋友") or "一位朋友"
        contact = clean_text(data.get("contact"), 40, "")
        message = clean_text(data.get("message"), 80, "悄悄支持了一下。") or "悄悄支持了一下。"
        target = clean_text(data.get("target"), 40, "")
        anonymous = bool(data.get("anonymous"))
        try:
            amount = float(data.get("amount"))
        except (TypeError, ValueError):
            raise ApiError(400, "金额不正确")
        if amount < 1 or amount > 100000:
            raise ApiError(400, "金额需要在 1 到 100000 元之间")
        amount = round(amount, 2)

        code = ""
        conn = connect_db()
        try:
            for _ in range(10):
                candidate = "WZ-" + secrets.token_hex(3).upper()
                exists = conn.execute("SELECT 1 FROM claims WHERE code = ?", (candidate,)).fetchone()
                if not exists:
                    code = candidate
                    break
            if not code:
                raise ApiError(500, "生成登记编号失败，请重试")
            cur = conn.execute(
                """INSERT INTO claims
                   (code, name, contact, amount, message, target, anonymous, status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)""",
                (code, name, contact, amount, message, target, 1 if anonymous else 0, now_iso()),
            )
            conn.commit()
            claim_id = cur.lastrowid
        finally:
            conn.close()
        self.send_json(201, {
            "ok": True,
            "claim": {
                "id": claim_id,
                "code": code,
                "amount": amount,
                "name": name,
                "message": message,
                "target": target,
                "anonymous": anonymous,
                "status": "pending",
            },
            "message": "登记已提交，等待管理员核对微信账单。",
        })

    def handle_review(self, new_status: str):
        data = self.read_json()
        try:
            claim_id = int(data.get("id"))
        except (TypeError, ValueError):
            raise ApiError(400, "登记编号不正确")
        conn = connect_db()
        try:
            cur = conn.execute(
                "UPDATE claims SET status = ?, reviewed_at = ? WHERE id = ? AND status = 'pending'",
                (new_status, now_iso(), claim_id),
            )
            conn.commit()
            if cur.rowcount == 0:
                raise ApiError(404, "这条登记不存在，或已经处理过了")
        finally:
            conn.close()
        self.send_json(200, {
            "ok": True,
            "status": new_status,
            "message": "已通过" if new_status == "verified" else "已驳回",
        })


def get_lan_ip() -> str:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
        sock.close()
        return ip
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return "127.0.0.1"


def main() -> None:
    parser = argparse.ArgumentParser(description="WAGZAO iPhone Duo 共享账本服务")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址，默认 0.0.0.0")
    parser.add_argument("--port", type=int, default=8787, help="端口，默认 8787")
    args = parser.parse_args()

    init_db()
    server = ThreadingHTTPServer((args.host, args.port), GiftHandler)
    lan_ip = get_lan_ip()
    print("")
    print("  ╭──────────────────────────────────────────────╮")
    print("  │   送 WAGZAO 一部 iPhone Duo · 共享账本服务   │")
    print("  ╰──────────────────────────────────────────────╯")
    print(f"  本机打开：http://127.0.0.1:{args.port}")
    print(f"  朋友访问：http://{lan_ip}:{args.port}")
    print(f"  核验台：  http://127.0.0.1:{args.port}/admin")
    print(f"  核验密钥：{ADMIN_KEY_FILE}")
    print("  停止服务：按 Control-C")
    print("")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止。")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
