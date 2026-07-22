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
        if not saved_risks:
            return jsonify({"success": True, "data": [], "message": "No risks detected or analysis could not be parsed — please try again"}), 200
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
            
        for r in risks:
            assignees_query = """
                SELECT s.name 
                FROM risk_assignments ra
                JOIN stakeholders s ON ra.stakeholder_id = s.id
                WHERE ra.risk_id = %s
            """
            assignees = execute_query(assignees_query, (r['id'],))
            r['assigned_stakeholders'] = [a['name'] for a in assignees] if assignees else []
            
            comments_query = """
                SELECT rc.id, rc.comment_text, rc.created_at, s.name as stakeholder_name, s.role 
                FROM risk_comments rc
                JOIN stakeholders s ON rc.stakeholder_id = s.id
                WHERE rc.risk_id = %s
                ORDER BY rc.created_at ASC
            """
            r['comments'] = execute_query(comments_query, (r['id'],))
            
        return jsonify({"success": True, "data": risks}), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@risk_bp.route('/<int:id>/escalate', methods=['PUT'])
def escalate_risk(id):
    try:
        data = request.json or {}
        assigned_to = data.get('assigned_to', [])
        initial_note = data.get('initial_note')
        
        from Controller.scheduling_controller import get_authenticated_user
        user_info = get_authenticated_user()
        user_id = user_info['sub']
        user = execute_query("SELECT email FROM users WHERE id = %s", (user_id,))
        manager_sh = execute_query("SELECT id FROM stakeholders WHERE email = %s", (user[0]['email'],))
        manager_id = manager_sh[0]['id'] if manager_sh else None
        
        from services.risk_service import escalate_risk_service
        result = escalate_risk_service(id, assigned_to, initial_note, manager_id)
        return jsonify({"success": True, "data": result, "message": "Risk escalated"}), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@risk_bp.route('/assigned', methods=['GET'])
def get_assigned_risks():
    try:
        from services.risk_service import get_assigned_risks_service
        from Controller.scheduling_controller import get_authenticated_user
        user_info = get_authenticated_user()
        user_id = user_info['sub']
        
        user = execute_query("SELECT email FROM users WHERE id = %s", (user_id,))
        if not user:
            return jsonify({"success": False, "message": "User not found"}), 404
        sh = execute_query("SELECT id FROM stakeholders WHERE email = %s", (user[0]['email'],))
        if not sh:
            return jsonify({"success": False, "message": "Stakeholder not found"}), 404
            
        stakeholder_id = sh[0]['id']
        risks = get_assigned_risks_service(stakeholder_id)
        return jsonify({"success": True, "data": risks}), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@risk_bp.route('/<int:id>/comments', methods=['POST'])
def add_risk_comment(id):
    try:
        data = request.json
        comment = data.get('comment')
        if not comment:
            return jsonify({"success": False, "message": "Missing comment text"}), 400
            
        from services.risk_service import add_risk_comment_service
        from Controller.scheduling_controller import get_authenticated_user
        user_info = get_authenticated_user()
        user_id = user_info['sub']
        
        user = execute_query("SELECT email FROM users WHERE id = %s", (user_id,))
        sh = execute_query("SELECT id FROM stakeholders WHERE email = %s", (user[0]['email'],))
        if not sh:
            return jsonify({"success": False, "message": "Stakeholder not found"}), 404
            
        stakeholder_id = sh[0]['id']
        result = add_risk_comment_service(id, stakeholder_id, comment)
        return jsonify({"success": True, "message": "Comment added", "data": result}), 201
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@risk_bp.route('/<int:id>/status', methods=['PUT'])
def update_risk_status(id):
    try:
        data = request.json
        status = data.get('status')
        if not status:
            return jsonify({"success": False, "message": "Missing status"}), 400
            
        from services.risk_service import update_risk_status_service
        update_risk_status_service(id, status)
        return jsonify({"success": True, "message": "Risk status updated"}), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500
