from typing import TypedDict, List
from langgraph.graph import StateGraph, START, END

from services.plan_service import generate_plan_service
from services.risk_service import detect_risks_service
from services.reporting_service import generate_weekly_service

class AgentState(TypedDict):
    plan_id: int
    status: str
    logs: List[str]
    app_name: str
    scope_desc: str
    plan_type: str

def plan_node(state: AgentState):
    if not state.get("plan_id"):
        plan_id = generate_plan_service(
            state.get("app_name", "TestApp"),
            state.get("scope_desc", "Automated orchestration"),
            state.get("plan_type", "KT")
        )
        state["plan_id"] = plan_id
        state["logs"].append(f"Plan generated with ID: {plan_id}")
    else:
        state["logs"].append(f"Using existing plan ID: {state['plan_id']}")
    return state

def risk_node(state: AgentState):
    plan_id = state.get("plan_id")
    if plan_id:
        try:
            risks = detect_risks_service(plan_id)
            state["logs"].append(f"Detected {len(risks)} risks")
        except Exception as e:
            state["logs"].append(f"Risk detection failed: {e}")
    return state

def report_node(state: AgentState):
    plan_id = state.get("plan_id")
    if plan_id:
        try:
            res = generate_weekly_service(plan_id)
            state["logs"].append(f"Generated weekly report: {res['filename']}")
            state["status"] = "completed"
        except Exception as e:
            state["logs"].append(f"Report generation failed: {e}")
            state["status"] = "failed"
    return state

def build_orchestrator():
    workflow = StateGraph(AgentState)
    
    workflow.add_node("plan", plan_node)
    workflow.add_node("risk", risk_node)
    workflow.add_node("report", report_node)
    
    workflow.add_edge(START, "plan")
    workflow.add_edge("plan", "risk")
    workflow.add_edge("risk", "report")
    workflow.add_edge("report", END)
    
    return workflow.compile()

orchestrator_app = build_orchestrator()

def run_workflow(app_name: str, scope_desc: str, plan_type: str):
    initial_state = {
        "plan_id": 0,
        "status": "started",
        "logs": [],
        "app_name": app_name,
        "scope_desc": scope_desc,
        "plan_type": plan_type
    }
    
    final_state = orchestrator_app.invoke(initial_state)
    return final_state
