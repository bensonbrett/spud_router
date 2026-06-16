"""Firewall routes: inbound rules and inter-VLAN rules."""
import secrets

from fastapi import APIRouter, Depends, HTTPException

from ..auth import require_auth
from ..models import InboundRule, InterVlanRule
from ..state import load_state, save_state

router = APIRouter(
    prefix="/api/firewall",
    tags=["firewall"],
    dependencies=[Depends(require_auth)],
)


@router.get("/inbound")
def list_inbound():
    return load_state().get("fw_inbound", [])


@router.post("/inbound")
def add_inbound(rule: InboundRule):
    state = load_state()
    rules = state.get("fw_inbound", [])
    rule.id = secrets.token_hex(4)
    rules.append(rule.model_dump())
    state["fw_inbound"] = rules
    save_state(state)
    return {"ok": True, "id": rule.id}


@router.delete("/inbound/{rule_id}")
def delete_inbound(rule_id: str):
    state  = load_state()
    before = len(state.get("fw_inbound", []))
    state["fw_inbound"] = [r for r in state.get("fw_inbound", []) if r.get("id") != rule_id]
    save_state(state)
    return {"removed": before - len(state["fw_inbound"])}


@router.get("/intervlan")
def list_intervlan():
    return load_state().get("fw_intervlan", [])


@router.post("/intervlan")
def add_intervlan(rule: InterVlanRule):
    state = load_state()
    rules = state.get("fw_intervlan", [])
    rule.id = secrets.token_hex(4)
    rules.append(rule.model_dump())
    state["fw_intervlan"] = rules
    save_state(state)
    return {"ok": True, "id": rule.id}


@router.delete("/intervlan/{rule_id}")
def delete_intervlan(rule_id: str):
    state  = load_state()
    before = len(state.get("fw_intervlan", []))
    state["fw_intervlan"] = [r for r in state.get("fw_intervlan", []) if r.get("id") != rule_id]
    save_state(state)
    return {"removed": before - len(state["fw_intervlan"])}
