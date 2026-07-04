# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
"""Firewall routes: inbound rules, inter-VLAN rules, outbound (egress) rules,
and port forwarding (DNAT)."""
import secrets

from fastapi import APIRouter, Depends, HTTPException

from ..auth import require_auth
from ..models import InboundRule, InterVlanRule, OutboundDefaultRequest, OutboundRule, PortForward
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


@router.get("/outbound")
def list_outbound():
    return load_state().get("fw_outbound", [])


@router.post("/outbound")
def add_outbound(rule: OutboundRule):
    state = load_state()
    rules = state.get("fw_outbound", [])
    rule.id = secrets.token_hex(4)
    rules.append(rule.model_dump())
    state["fw_outbound"] = rules
    save_state(state)
    return {"ok": True, "id": rule.id}


@router.delete("/outbound/{rule_id}")
def delete_outbound(rule_id: str):
    state  = load_state()
    before = len(state.get("fw_outbound", []))
    state["fw_outbound"] = [r for r in state.get("fw_outbound", []) if r.get("id") != rule_id]
    save_state(state)
    return {"removed": before - len(state["fw_outbound"])}


@router.get("/outbound/default")
def get_outbound_default():
    return {"default": load_state().get("fw_outbound_default", "allow")}


@router.put("/outbound/default")
def set_outbound_default(req: OutboundDefaultRequest):
    state = load_state()
    state["fw_outbound_default"] = req.default
    save_state(state)
    return {"ok": True, "default": req.default}


@router.get("/port-forward")
def list_port_forwards():
    return load_state().get("port_forwards", [])


@router.post("/port-forward")
def add_port_forward(forward: PortForward):
    state = load_state()
    forwards = state.get("port_forwards", [])
    forward.id = secrets.token_hex(4)
    forwards.append(forward.model_dump())
    state["port_forwards"] = forwards
    save_state(state)
    return {"ok": True, "id": forward.id}


@router.put("/port-forward/{forward_id}")
def update_port_forward(forward_id: str, forward: PortForward):
    state = load_state()
    forwards = state.get("port_forwards", [])
    idx = next((i for i, f in enumerate(forwards) if f.get("id") == forward_id), None)
    if idx is None:
        raise HTTPException(status_code=404, detail=f"Port forward {forward_id} not found")

    # URL id is authoritative — the body isn't required to (and needn't) carry one.
    forward.id = forward_id
    forwards[idx] = forward.model_dump()
    state["port_forwards"] = forwards
    save_state(state)
    return {"ok": True}


@router.delete("/port-forward/{forward_id}")
def delete_port_forward(forward_id: str):
    state  = load_state()
    before = len(state.get("port_forwards", []))
    state["port_forwards"] = [f for f in state.get("port_forwards", []) if f.get("id") != forward_id]
    save_state(state)
    return {"removed": before - len(state["port_forwards"])}
