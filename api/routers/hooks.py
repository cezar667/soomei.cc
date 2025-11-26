from fastapi import APIRouter

from api.repositories.sql_repository import SQLRepository

router = APIRouter(prefix="/hooks", tags=["hooks"])
_sql_repo = SQLRepository()


@router.post("/themembers")
def themembers(payload: dict):
    uid = payload.get("uid")
    if not uid:
        return {"ok": True}
    status = payload.get("status", "ok")
    new_status = "blocked" if status in ("blocked", "delinquent") else ("active" if status == "ok" else None)
    if new_status:
        _sql_repo.update_card_status(uid, new_status, billing_status=status)
    return {"ok": True}
