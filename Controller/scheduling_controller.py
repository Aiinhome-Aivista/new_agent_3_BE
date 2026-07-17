from flask import Blueprint, request, jsonify
from db import execute_query, execute_write
import jwt
from config import Config

scheduling_bp = Blueprint('scheduling_bp', __name__)

def get_authenticated_user():
    auth_header = request.headers.get('Authorization')
    if not auth_header or not auth_header.startswith('Bearer '):
        raise Exception("Missing or invalid Authorization header")
    
    token = auth_header.split(' ')[1]
    try:
        payload = jwt.decode(token, Config.SECRET_KEY, algorithms=["HS256"])
        return payload
    except jwt.ExpiredSignatureError:
        raise Exception("Token has expired")
    except jwt.InvalidTokenError:
        raise Exception("Invalid token")

@scheduling_bp.route('/meetings', methods=['POST'])
def create_meeting():
    try:
        user_info = get_authenticated_user()
        user_id = user_info['sub']
        user_query = "SELECT id, full_name, email FROM users WHERE id = %s AND is_active = 1 LIMIT 1"
        users = execute_query(user_query, (user_id,))
        if not users:
            return jsonify({"success": False, "message": "Authenticated user not found or inactive"}), 401
        logged_in_user = users[0]
        organizer_id = logged_in_user['id']
    except Exception as auth_err:
        return jsonify({"success": False, "message": str(auth_err)}), 401

    data = request.json or {}
    required_fields = ['plan_id', 'title', 'scheduled_at']
    from guardrails import input_rail
    passed, reason = input_rail(data, required_fields, "/api/schedule/")
    if not passed:
        return jsonify({"success": False, "message": reason}), 400
        
    try:
        # Validate participants
        stakeholder_ids = data.get('stakeholder_ids') or data.get('attendees') or []
        if not stakeholder_ids:
            return jsonify({"success": False, "message": "At least one participant must be selected"}), 400
            
        format_strings = ','.join(['%s'] * len(stakeholder_ids))
        query_roles = f"""
            SELECT id FROM stakeholders 
            WHERE id IN ({format_strings}) 
            AND (role = 'incoming_member' OR role = 'Incoming Team Member (Knowledge Receiver)')
        """
        db_res = execute_query(query_roles, tuple(stakeholder_ids))
        valid_stakeholder_ids = [row['id'] for row in db_res]
        
        if len(valid_stakeholder_ids) != len(stakeholder_ids):
            return jsonify({"success": False, "message": "Invalid participants detected. Only Incoming Team Members (Knowledge Receivers) can be added as meeting participants."}), 400

        query = """
            INSERT INTO meetings (plan_id, title, scheduled_at, organizer_id, description, meeting_link)
            VALUES (%s, %s, %s, %s, %s, %s)
        """
        params = (
            data['plan_id'], 
            data['title'], 
            data['scheduled_at'], 
            organizer_id,
            data.get('description'),
            data.get('meeting_link') or data.get('link')
        )
        meeting_id = execute_write(query, params)
        
        # Save optional attendee stakeholder IDs to attendance table
        for sh_id in valid_stakeholder_ids:
            try:
                execute_write(
                    "INSERT INTO attendance (meeting_id, stakeholder_id) VALUES (%s, %s)",
                    (meeting_id, sh_id)
                )
            except Exception as att_err:
                print(f"Error inserting attendance for stakeholder {sh_id}: {att_err}")

        # Trigger background email notifications
        from services.notification_service import trigger_meeting_notifications
        trigger_meeting_notifications(meeting_id)

        return jsonify({
            "success": True, 
            "data": {"id": meeting_id}, 
            "message": "Meeting created successfully. Email notifications initiated."
        }), 201
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
            
        from services.tracking_service import get_meeting_attendance_rate
        for m in meetings:
            m['attendance_rate_percent'] = get_meeting_attendance_rate(m['id'])
            
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
        query = "SELECT title, scheduled_at FROM meetings WHERE id = %s"
        meeting = execute_query(query, (id,))
        if not meeting:
            return jsonify({"success": False, "message": "Meeting not found"}), 404
            
        # Trigger background email notifications
        from services.notification_service import trigger_meeting_notifications
        trigger_meeting_notifications(id)
        return jsonify({"success": True, "message": "Notification sent successfully"}), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500
