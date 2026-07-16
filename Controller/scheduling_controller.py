from flask import Blueprint, request, jsonify
from db import execute_query, execute_write

scheduling_bp = Blueprint('scheduling_bp', __name__)

@scheduling_bp.route('/meetings', methods=['POST'])
def create_meeting():
    data = request.json
    required_fields = ['plan_id', 'title', 'scheduled_at']
    from guardrails import input_rail
    passed, reason = input_rail(data, required_fields, "/api/schedule/")
    if not passed:
        return jsonify({"success": False, "message": reason}), 400
        
    try:
        query = """
            INSERT INTO meetings (plan_id, title, scheduled_at, organizer_id)
            VALUES (%s, %s, %s, %s)
        """
        params = (data['plan_id'], data['title'], data['scheduled_at'], data.get('organizer_id'))
        meeting_id = execute_write(query, params)
        return jsonify({"success": True, "data": {"id": meeting_id}, "message": "Meeting created successfully"}), 201
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@scheduling_bp.route('/meetings', methods=['GET'])
def get_meetings():
    plan_id = request.args.get('plan_id')
    try:
        if plan_id:
            query = "SELECT * FROM meetings WHERE plan_id = %s ORDER BY scheduled_at ASC"
            meetings = execute_query(query, (plan_id,))
        else:
            query = "SELECT * FROM meetings ORDER BY scheduled_at ASC"
            meetings = execute_query(query)
        return jsonify({"success": True, "data": meetings}), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@scheduling_bp.route('/meetings/<int:id>/status', methods=['PUT'])
def update_meeting_status(id):
    data = request.json
    if 'status' not in data:
        return jsonify({"success": False, "message": "Missing status field"}), 400
        
    try:
        query = "UPDATE meetings SET status = %s WHERE id = %s"
        execute_write(query, (data['status'], id))
        return jsonify({"success": True, "message": "Meeting status updated"}), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@scheduling_bp.route('/meetings/<int:id>/notify', methods=['POST'])
def notify_meeting(id):
    try:
        # Simulate sending a notification
        query = "SELECT title, scheduled_at FROM meetings WHERE id = %s"
        meeting = execute_query(query, (id,))
        if not meeting:
            return jsonify({"success": False, "message": "Meeting not found"}), 404
            
        print(f"NOTIFICATION SENT: Reminder for meeting '{meeting[0]['title']}' at {meeting[0]['scheduled_at']}")
        return jsonify({"success": True, "message": "Notification sent successfully"}), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500
