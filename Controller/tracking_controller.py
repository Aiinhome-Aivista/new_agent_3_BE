from flask import Blueprint, request, jsonify
from db import execute_query, execute_write

tracking_bp = Blueprint('tracking_bp', __name__)

@tracking_bp.route('/attendance', methods=['POST'])
def mark_attendance():
    data = request.json
    required_fields = ['meeting_id', 'stakeholder_id', 'attended']
    from guardrails import input_rail
    passed, reason = input_rail(data, required_fields, "/api/tracking/attendance")
    if not passed:
        return jsonify({"success": False, "message": reason}), 400
        
    try:
        # Upsert logic since we have UNIQUE(meeting_id, stakeholder_id)
        query = """
            INSERT INTO attendance (meeting_id, stakeholder_id, attended, notes)
            VALUES (%s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE attended = VALUES(attended), notes = VALUES(notes)
        """
        params = (data['meeting_id'], data['stakeholder_id'], data['attended'], data.get('notes', ''))
        execute_write(query, params)
        return jsonify({"success": True, "message": "Attendance marked successfully"}), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@tracking_bp.route('/completion', methods=['POST'])
def update_completion():
    data = request.json
    required_fields = ['plan_id', 'topic', 'completion_percent']
    from guardrails import input_rail
    passed, reason = input_rail(data, required_fields, "/api/tracking/completion")
    if not passed:
        return jsonify({"success": False, "message": reason}), 400
        
    try:
        from services.tracking_service import update_completion_service
        update_completion_service(data['plan_id'], data['topic'], data['completion_percent'])
        return jsonify({"success": True, "message": "Completion updated successfully"}), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@tracking_bp.route('/plan/<int:plan_id>/summary', methods=['GET'])
def get_plan_summary(plan_id):
    try:
        from services.tracking_service import get_plan_summary_service
        summary_data = get_plan_summary_service(plan_id)
        
        return jsonify({
            "success": True, 
            "data": summary_data
        }), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@tracking_bp.route('/plan/<int:plan_id>/topics', methods=['GET'])
def get_plan_topics(plan_id):
    try:
        query = "SELECT * FROM completion_tracking WHERE plan_id = %s"
        topics = execute_query(query, (plan_id,))
        return jsonify({"success": True, "data": topics}), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500
