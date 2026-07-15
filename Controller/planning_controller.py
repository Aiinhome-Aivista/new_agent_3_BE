from flask import Blueprint, request, jsonify
from db import execute_query, execute_write
from llm_service import call_llm
import json

planning_bp = Blueprint('planning_bp', __name__)

@planning_bp.route('/generate', methods=['POST'])
def generate_plan():
    data = request.json
    required_fields = ['application_name', 'scope_description', 'plan_type']
    if not all(field in data for field in required_fields):
        return jsonify({"success": False, "message": "Missing required fields"}), 400
        
    app_name = data['application_name']
    scope = data['scope_description']
    plan_type = data['plan_type']
    created_by = data.get('created_by') # Optional
    
    prompt = f"""
    You are an expert IT Project Manager. Create a detailed {plan_type} plan for the application '{app_name}'.
    Scope: {scope}
    
    Format the output as a clean, structured Markdown document including:
    1. Objectives
    2. Target Audience
    3. Sessions / Topics Breakdown (with estimated duration in hours)
    4. Expected Outcomes
    
    Only output the markdown content, no conversational filler.
    """
    
    try:
        # Call LLM
        generated_content = call_llm(prompt)
        
        # Save to DB as draft
        query = """
            INSERT INTO kt_plans (application_name, scope_description, plan_type, generated_content, status, created_by)
            VALUES (%s, %s, %s, %s, 'draft', %s)
        """
        params = (app_name, scope, plan_type, generated_content, created_by)
        plan_id = execute_write(query, params)
        
        return jsonify({
            "success": True, 
            "data": {
                "id": plan_id,
                "generated_content": generated_content,
                "status": "draft"
            },
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

@planning_bp.route('/<int:id>/approve', methods=['PUT'])
def approve_plan(id):
    try:
        query = "UPDATE kt_plans SET status = 'approved' WHERE id = %s"
        execute_write(query, (id,))
        return jsonify({"success": True, "message": "Plan approved successfully"}), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500
