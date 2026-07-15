from flask import Blueprint, request, jsonify
from db import execute_query, execute_write

tracking_bp = Blueprint('tracking_bp', __name__)

@tracking_bp.route('/attendance', methods=['POST'])
def mark_attendance():
    data = request.json
    required_fields = ['meeting_id', 'stakeholder_id', 'attended']
    if not all(field in data for field in required_fields):
        return jsonify({"success": False, "message": "Missing required fields"}), 400
        
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
    if not all(field in data for field in required_fields):
        return jsonify({"success": False, "message": "Missing required fields"}), 400
        
    try:
        query = """
            INSERT INTO completion_tracking (plan_id, topic, completion_percent)
            VALUES (%s, %s, %s)
            ON DUPLICATE KEY UPDATE completion_percent = VALUES(completion_percent)
        """
        params = (data['plan_id'], data['topic'], data['completion_percent'])
        execute_write(query, params)
        return jsonify({"success": True, "message": "Completion updated successfully"}), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@tracking_bp.route('/plan/<int:plan_id>/summary', methods=['GET'])
def get_plan_summary(plan_id):
    try:
        # Get average completion
        comp_query = "SELECT AVG(completion_percent) as avg_completion FROM completion_tracking WHERE plan_id = %s"
        comp_res = execute_query(comp_query, (plan_id,))
        avg_completion = float(comp_res[0]['avg_completion']) if comp_res and comp_res[0]['avg_completion'] else 0
        
        # Get attendance rate
        att_query = """
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN attended = 1 THEN 1 ELSE 0 END) as attended
            FROM attendance a
            JOIN meetings m ON a.meeting_id = m.id
            WHERE m.plan_id = %s
        """
        att_res = execute_query(att_query, (plan_id,))
        total_attendance = int(att_res[0]['total']) if att_res and att_res[0]['total'] else 0
        attended_count = int(att_res[0]['attended']) if att_res and att_res[0]['attended'] else 0
        attendance_rate = (attended_count / total_attendance * 100) if total_attendance > 0 else 0
        
        return jsonify({
            "success": True, 
            "data": {
                "avg_completion_percent": round(avg_completion, 2),
                "attendance_rate_percent": round(attendance_rate, 2)
            }
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
