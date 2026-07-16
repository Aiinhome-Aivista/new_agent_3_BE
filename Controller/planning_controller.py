from flask import Blueprint, request, jsonify
from db import execute_query, execute_write
from llm_service import call_llm
import json

planning_bp = Blueprint('planning_bp', __name__)

@planning_bp.route('/generate', methods=['POST'])
def generate_plan():
    data = request.json
    required_fields = ['application_name', 'scope_description', 'plan_type']
    from guardrails import input_rail
    passed, reason = input_rail(data, required_fields, "/api/plans/generate")
    if not passed:
        return jsonify({"success": False, "message": reason}), 400
        
    app_name = data['application_name']
    scope = data['scope_description']
    plan_type = data['plan_type']
    created_by = data.get('created_by') # Optional
    
    
    try:
        from services.plan_service import generate_plan_service
        result_data = generate_plan_service(app_name, scope, plan_type, created_by)
        
        return jsonify({
            "success": True, 
            "data": result_data,
            "message": "Plan generated successfully"
        }), 201
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@planning_bp.route('/', methods=['GET'])
def get_plans():
    try:
        query = "SELECT * FROM kt_plans ORDER BY created_at DESC"
        plans = execute_query(query)
        return jsonify({"success": True, "data": plans}), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@planning_bp.route('/<int:plan_id>', methods=['GET'])
def get_plan(plan_id):
    try:
        query = "SELECT * FROM kt_plans WHERE id = %s"
        plan = execute_query(query, (plan_id,))
        if not plan:
            return jsonify({"success": False, "message": "Plan not found"}), 404
        return jsonify({"success": True, "data": plan[0]}), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@planning_bp.route('/workflow', methods=['POST'])
def run_full_workflow():
    data = request.json
    required_fields = ['application_name', 'scope_description', 'plan_type']
    from guardrails import input_rail
    passed, reason = input_rail(data, required_fields, "/api/plans/workflow")
    if not passed:
        return jsonify({"success": False, "message": reason}), 400
        
    app_name = data['application_name']
    scope = data['scope_description']
    plan_type = data['plan_type']
    
    try:
        from orchestrator import run_workflow
        final_state = run_workflow(app_name, scope, plan_type)
        return jsonify({"success": True, "message": "Workflow completed", "data": final_state}), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@planning_bp.route('/<int:id>/approve', methods=['PUT'])
def approve_plan(id):
    try:
        query = "UPDATE kt_plans SET status = 'approved' WHERE id = %s"
        execute_write(query, (id,))
        return jsonify({"success": True, "message": "Plan approved successfully"}), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500
