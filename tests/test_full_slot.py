"""满档占位回归：先红后绿。

对应口径（甲路径，见 docs/满档拒占说明.md）：
- 可占席 = 容量 - 已订 - 占位中；不足则占位零增，整笔回滚，转候补并记满档审计。
- 两人抢满场必须抢档锁，后到只留争用痕，不写业务。
- 拒绝写与审计同败：审计写失败时，候补与占位都不得落库。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import app  # noqa: E402
import db as dbmod  # noqa: E402
from db import connect, init_db  # noqa: E402

FULL_SEED = ROOT / "fixtures" / "full_slot_seed.json"
FULL_CODE = "S-FULL-01"
FULL_CAPACITY = 4
FULL_HELD = 4


@pytest.fixture(autouse=True)
def _fresh_db(tmp_path, monkeypatch):
    db = tmp_path / "t.db"
    monkeypatch.setattr(dbmod, "DB_PATH", db)
    if db.exists():
        db.unlink()
    init_db()
    yield


def _seed_full_slot() -> None:
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


def _login(c: TestClient, user: str = "admin") -> dict[str, str]:
    r = c.post("/api/login", json={"username": user, "password": "123456"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_full_slot_hold_refused_waitlisted_and_five_surfaces_agree():
    """满档仍占必须失败：占位零增、转候补、可占席 0、对账灯绿、满档审计。"""
    _seed_full_slot()
    with TestClient(app) as c:
        h = _login(c)
        holds_before = c.get("/api/holds", headers=h).json()["items"]
        wait_before = c.get("/api/waitlist", headers=h).json()["items"]

        r = c.post(
            "/api/holds",
            headers=h,
            json={"slot_code": FULL_CODE, "party_key": "P-SCALPER", "seats": 1},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["disposition"] != "占位", f"满档仍成功占位：{body}"
        assert body["disposition"] == "候补"
        assert "hold_id" not in body

        # 占位中：零增
        holds_after = c.get("/api/holds", headers=h).json()["items"]
        assert len(holds_after) == len(holds_before)
        assert not any(x["party_key"] == "P-SCALPER" for x in holds_after)
        assert (
            sum(x["seats"] for x in holds_after if x["status"] == "占位中" and x["slot_code"] == FULL_CODE)
            == FULL_HELD
        )

        # 候补：不再空空
        wait_after = c.get("/api/waitlist", headers=h).json()["items"]
        mine = [w for w in wait_after if w["party_key"] == "P-SCALPER"]
        assert len(mine) == 1
        assert mine[0]["status"] == "候补中"
        assert mine[0]["reason"] == "满档"
        assert len(wait_after) == len(wait_before) + 1

        # 可占席 + 售罄同态：占位中必须扣进可占席
        slot = c.get(f"/api/slots/{FULL_CODE}", headers=h).json()
        assert slot["booked"] == 0
        assert slot["pending_seats"] == FULL_HELD
        assert slot["free_seats"] == 0
        assert slot["display_free"] == 0
        assert slot["sold_out"] is True

        # 对账灯：正式册同源，同时确认满档不分裂
        recon = c.get("/api/reconcile", headers=h).json()
        item = [i for i in recon["items"] if i["slot_code"] == FULL_CODE][0]
        assert item["ok"] is True
        assert item["capacity_ok"] is True
        assert item["pending_seats"] == FULL_HELD
        assert item["free_seats"] == 0
        assert item["sold_out"] is True
        assert recon["all_ok"] is True

        # 审计：满档拒占留痕
        audits = c.get("/api/audit", headers=h).json()["items"]
        assert any(
            a["action"] == "hold_full_reject" and a["target"] == "P-SCALPER"
            for a in audits
        )


def test_two_racers_full_slot_later_only_leaves_contention_trace():
    """两人抢满场：先到持档锁，后到 409 且只留争用痕，不写业务、不留满档审计。"""
    _seed_full_slot()
    with TestClient(app) as c:
        admin = _login(c, "admin")
        clerk = _login(c, "clerk")

        lock = c.post(
            "/api/locks/acquire",
            headers=admin,
            json={"lock_key": f"slot:{FULL_CODE}"},
        )
        assert lock.status_code == 200, lock.text

        holds_before = c.get("/api/holds", headers=admin).json()["items"]
        wait_before = c.get("/api/waitlist", headers=admin).json()["items"]
        audit_ids_before = {a["id"] for a in c.get("/api/audit", headers=admin).json()["items"]}

        r = c.post(
            "/api/holds",
            headers=clerk,
            json={"slot_code": FULL_CODE, "party_key": "P-LATE", "seats": 1},
        )
        assert r.status_code == 409, r.text
        assert "争用" in r.json()["detail"]

        holds_after = c.get("/api/holds", headers=admin).json()["items"]
        wait_after = c.get("/api/waitlist", headers=admin).json()["items"]
        assert len(holds_after) == len(holds_before)
        assert len(wait_after) == len(wait_before)

        new_audits = [
            a
            for a in c.get("/api/audit", headers=admin).json()["items"]
            if a["id"] not in audit_ids_before
        ]
        assert not any(
            a["action"] in ("hold_create", "hold_to_waitlist", "hold_full_reject")
            for a in new_audits
        )

        contention = c.get("/api/contention", headers=admin).json()["items"]
        assert any(
            x["lock_key"] == f"slot:{FULL_CODE}"
            and x["actor"] == "clerk"
            and x["action"] == "争用"
            for x in contention
        ), "后到争用未留痕"


def test_full_reject_audit_failure_rolls_back_refusal_write(monkeypatch):
    """拒绝写与审计同败：满档审计写失败时，候补与占位均不得落库。"""
    _seed_full_slot()
    import audit_svc

    def boom(*args, **kwargs):
        raise RuntimeError("audit down")

    monkeypatch.setattr(audit_svc, "record_full_reject", boom)

    with TestClient(app, raise_server_exceptions=False) as c:
        h = _login(c)
        r = c.post(
            "/api/holds",
            headers=h,
            json={"slot_code": FULL_CODE, "party_key": "P-ATOMIC", "seats": 1},
        )
        assert r.status_code >= 500, f"审计失败仍返回业务成功：{r.status_code} {r.text}"

    with connect() as conn:
        holds = conn.execute(
            "SELECT COUNT(*) c FROM holds WHERE party_key='P-ATOMIC'"
        ).fetchone()["c"]
        waits = conn.execute(
            "SELECT COUNT(*) c FROM waitlist WHERE party_key='P-ATOMIC'"
        ).fetchone()["c"]
        audits = conn.execute(
            "SELECT COUNT(*) c FROM audit_trail WHERE target='P-ATOMIC'"
        ).fetchone()["c"]
    assert holds == 0
    assert waits == 0
    assert audits == 0
