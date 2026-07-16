from flask import Blueprint, request, jsonify
from db import execute_query, execute_write
from llm_service import call_llm
import json

risk_bp = Blueprint('risk_bp', __name__)

@risk_bp.route('/detect', methods=['POST'])
def detect_risks():
    data = request.json
    if 'plan_id' not in data:
        return jsonify({"success": False, "message": "Missing plan_id"}), 400
        
    plan_id = data['plan_id']
    
    try:
        from services.risk_service import detect_risks_service
        saved_risks = detect_risks_service(plan_id)
        return jsonify({"success": True, "data": saved_risks, "message": "Risks detected and logged"}), 200
        
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@risk_bp.route('/', methods=['GET'])
def get_risks():
    plan_id = request.args.get('plan_id')
    try:
        if plan_id:
            query = "SELECT * FROM risks WHERE plan_id = %s ORDER BY created_at DESC"
            risks = execute_query(query, (plan_id,))
        else:
            query = "SELECT * FROM risks ORDER BY created_at DESC"
            risks = execute_query(query)
        return jsonify({"success": True, "data": risks}), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@risk_bp.route('/<int:id>/escalate', methods=['PUT'])
def escalate_risk(id):
    try:
        from services.risk_service import escalate_risk_service
        escalate_risk_service(id)
        return jsonify({"success": True, "message": "Risk escalated"}), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500
