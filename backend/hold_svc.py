"""占位：容量校验、并发锁、候补分流。"""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from auth import AuthHeader, require_user, require_writer
import audit_svc
from db import connect, write_audit
from locks import release, require_lock
from slots_svc import seat_view

router = APIRouter(tags=["holds"])


class HoldIn(BaseModel):
    slot_code: str
    party_key: str
    seats: int = Field(ge=1, le=50)


@router.get("/api/holds")
def list_holds(authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        rows = conn.execute(
            """SELECT h.*, s.code AS slot_code FROM holds h
               JOIN slots s ON s.id=h.slot_id ORDER BY h.id DESC LIMIT 200"""
        ).fetchall()
        return {"items": [dict(r) for r in rows]}


@router.post("/api/holds")
def create_hold(body: HoldIn, authorization: AuthHeader = None):
    user = require_writer(authorization)
    lock_key = f"slot:{body.slot_code}"
    with connect() as conn:
        require_lock(conn, lock_key, user)
        try:
            slot = conn.execute(
                "SELECT * FROM slots WHERE code=?", (body.slot_code,)
            ).fetchone()
            if not slot:
                raise HTTPException(404, "档期不存在")
            if slot["status"] != "开放":
                raise HTTPException(409, "档期未开放，禁止占位")
            view = seat_view(conn, slot)
            free = view["free_seats"]
            now = datetime.now().isoformat(timespec="seconds")
            expire = (datetime.now() + timedelta(minutes=30)).isoformat(timespec="seconds")
            if body.seats > free:
                # 甲路径：满档整笔回滚拒占，占位零增，转候补并记满档审计。
                full = free <= 0
                reason = "满档" if full else "容量不足"
                conn.execute(
                    """INSERT INTO waitlist(slot_id,party_key,seats,reason,status,created_at)
                       VALUES (?,?,?,?,?,?)""",
                    (slot["id"], body.party_key, body.seats, reason, "候补中", now),
                )
                if full:
                    # 走模块属性，便于审计故障注入测验证同败。
                    audit_svc.record_full_reject(
                        conn,
                        user,
                        body.party_key,
                        {
                            "slot": body.slot_code,
                            "capacity": view["capacity"],
                            "booked": view["booked"],
                            "pending_seats": view["pending_seats"],
                            "free_seats": free,
                            "want": body.seats,
                        },
                    )
                else:
                    write_audit(
                        conn,
                        user,
                        "hold_to_waitlist",
                        body.party_key,
                        {"slot": body.slot_code, "free": free, "want": body.seats},
                    )
                # 候补与审计同事务提交；任一写失败则整笔回滚，拒绝写与审计同败。
                conn.commit()
                return {"disposition": "候补", "reason": reason, "free": free}
            cur = conn.execute(
                """INSERT INTO holds(slot_id,party_key,seats,status,expire_at,created_at)
                   VALUES (?,?,?,?,?,?)""",
                (slot["id"], body.party_key, body.seats, "占位中", expire, now),
            )
            write_audit(
                conn,
                user,
                "hold_create",
                body.party_key,
                {"slot": body.slot_code, "seats": body.seats, "hold_id": cur.lastrowid},
            )
            conn.commit()
            return {"disposition": "占位", "hold_id": cur.lastrowid, "expire_at": expire}
        except Exception:
            # 甲路径：任一步失败（含满档审计写失败）整笔回滚，拒绝写与审计同败。
            conn.rollback()
            raise
        finally:
            release(conn, lock_key, user)
            conn.commit()
