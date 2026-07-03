"""
SNMP agent config: GET/PUT /api/snmp.

Community strings are write-only from the API's perspective: GET replaces
a stored (non-empty) community with SNMP_MASKED_SENTINEL, and PUT treats
that same sentinel as "leave the stored value unchanged" — the only way to
actually change a community is to submit a real, different value.
"""
from fastapi import APIRouter, Depends

from ..auth import require_auth
from ..models import SNMP_MASKED_SENTINEL, SnmpConfig
from ..state import load_state, save_state

router = APIRouter(tags=["snmp"], dependencies=[Depends(require_auth)])


def _mask(snmp: dict) -> dict:
    masked = dict(snmp)
    if masked.get("community_ro"):
        masked["community_ro"] = SNMP_MASKED_SENTINEL
    if masked.get("community_rw"):
        masked["community_rw"] = SNMP_MASKED_SENTINEL
    return masked


@router.get("/api/snmp")
def get_snmp():
    state = load_state()
    return _mask(state.get("snmp", SnmpConfig().model_dump()))


@router.put("/api/snmp")
def set_snmp(config: SnmpConfig):
    state = load_state()
    current = state.get("snmp", {})
    data = config.model_dump()

    if data["community_ro"] == SNMP_MASKED_SENTINEL:
        data["community_ro"] = current.get("community_ro", "")
    if data["community_rw"] == SNMP_MASKED_SENTINEL:
        data["community_rw"] = current.get("community_rw", "")

    state["snmp"] = data
    save_state(state)
    return {"ok": True}
