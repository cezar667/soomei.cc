from fastapi import APIRouter

from api.repositories.json_storage import load, save, db_defaults

router = APIRouter(prefix="/hooks", tags=["hooks"])


@router.post("/themembers")
def themembers(payload: dict):
    db = db_defaults(load())
    uid = payload.get("uid")
    if not uid or uid not in db.get("cards", {}):
        return {"ok": True}
    status = payload.get("status", "ok")
    card = db["cards"].get(uid, {})
    card["billing_status"] = status
    if status in ("blocked", "delinquent"):
        card["status"] = "blocked"
    elif status == "ok":
        card["status"] = "active"
    db["cards"][uid] = card
    save(db)
    return {"ok": True}
