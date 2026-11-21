"""对账：档期 booked 与正式册 seats 同源校验。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from auth import AuthHeader, require_user
from db import connect
from slots_svc import seat_view

router = APIRouter(tags=["reconcile"])


def _reconcile_item(conn, s) -> dict:
    booked_sum = conn.execute(
        """SELECT COALESCE(SUM(seats),0) s FROM bookings
           WHERE slot_id=? AND status='已确认'""",
        (s["id"],),
    ).fetchone()["s"]
    view = seat_view(conn, s)
    booked_ok = int(booked_sum) == int(s["booked"])
    capacity_ok = view["booked"] + view["pending_seats"] <= view["capacity"]
    return {
        "slot_code": s["code"],
        "slot_booked": s["booked"],
        "booking_seats": booked_sum,
        "pending_seats": view["pending_seats"],
        "free_seats": view["free_seats"],
        "sold_out": view["sold_out"],
        "generation": s["generation"],
        "status": s["status"],
        "ok": booked_ok,
        "capacity_ok": capacity_ok,
        "light_ok": booked_ok and capacity_ok,
    }


@router.get("/api/reconcile")
def reconcile_all(authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        slots = conn.execute("SELECT * FROM slots ORDER BY code").fetchall()
        items = [_reconcile_item(conn, s) for s in slots]
        return {
            "items": items,
            "all_ok": all(i["light_ok"] for i in items),
        }


@router.get("/api/reconcile/{code}")
def reconcile_one(code: str, authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        s = conn.execute("SELECT * FROM slots WHERE code=?", (code,)).fetchone()
        if not s:
            raise HTTPException(404, "档期不存在")
        item = _reconcile_item(conn, s)
        item["open_holds"] = conn.execute(
            "SELECT COUNT(*) c FROM holds WHERE slot_id=? AND status='占位中'",
            (s["id"],),
        ).fetchone()["c"]
        return item
