"""复现假绿：满场种子（容量 6，已订 4，占位中 2）后再占 1 席仍成功。

本脚本不是 pytest 用例，只用于在修缺陷前证明缺陷可复现：
    /tmp/venv/bin/python tests/repro_full_slot_fake_green.py

预期（缺陷版）：disposition=占位，hold 行数 +1，可占席仍被算成 2。
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import db as dbmod  # noqa: E402
from db import connect  # noqa: E402

FULL_SEED = ROOT / "fixtures" / "full_slot_seed.json"


def seed_full_slot() -> None:
    data = json.loads(FULL_SEED.read_text(encoding="utf-8"))
    with connect() as conn:
        for s in data["slots"]:
            conn.execute(
                """INSERT INTO slots(code,title,start_at,end_at,capacity,booked,status,generation)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    s["code"],
                    s["title"],
                    s["start_at"],
                    s["end_at"],
                    s["capacity"],
                    s.get("booked", 0),
                    s["status"],
                    s.get("generation", 1),
                ),
            )
        for h in data["holds"]:
            slot = conn.execute(
                "SELECT id FROM slots WHERE code=?", (h["slot_code"],)
            ).fetchone()
            conn.execute(
                """INSERT INTO holds(slot_id,party_key,seats,status,expire_at,created_at)
                   VALUES (?,?,?,?,?,?)""",
                (
                    slot["id"],
                    h["party_key"],
                    h["seats"],
                    h["status"],
                    h["expire_at"],
                    h["created_at"],
                ),
            )
        conn.commit()


def main() -> int:
    dbmod.DB_PATH = Path(tempfile.mkdtemp(prefix="slotbook-repro-")) / "repro.db"
    dbmod.init_db()
    seed_full_slot()

    from app import app
    from fastapi.testclient import TestClient

    with TestClient(app) as c:
        token = c.post(
            "/api/login", json={"username": "admin", "password": "123456"}
        ).json()["token"]
        headers = {"Authorization": f"Bearer {token}"}

        before = c.get("/api/holds", headers=headers).json()["items"]
        slot = c.get("/api/slots/S-FULL-01", headers=headers).json()
        resp = c.post(
            "/api/holds",
            headers=headers,
            json={"slot_code": "S-FULL-01", "party_key": "P-SCALPER", "seats": 1},
        )
        after = c.get("/api/holds", headers=headers).json()["items"]
        waits = c.get("/api/waitlist", headers=headers).json()["items"]

        print("== 满场种子 ==")
        print(json.dumps(slot, ensure_ascii=False, indent=2))
        print("== 再占 1 席 ==")
        print(resp.status_code, json.dumps(resp.json(), ensure_ascii=False))
        print("hold 行数:", len(before), "->", len(after))
        print("候补行数:", len(waits))
        print(
            "假绿判定:",
            "是（满场仍成功占位）"
            if resp.status_code == 200 and resp.json().get("disposition") == "占位"
            else "否",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
