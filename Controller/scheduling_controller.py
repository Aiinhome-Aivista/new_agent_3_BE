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
    print(f"DEBUG: Token prefix: {token[:10]}... Length: {len(token)}")
    print(f"DEBUG: SECRET_KEY used: {Config.SECRET_KEY}")
    try:
        payload = jwt.decode(token, Config.SECRET_KEY, algorithms=["HS256"])
        return payload
    except jwt.ExpiredSignatureError:
        print("DEBUG: Token has expired")
        raise Exception("Token has expired")
    except jwt.InvalidTokenError as e:
        print(f"DEBUG: InvalidTokenError details: {e}")
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
        print(f"DEBUG AUTH_ERR: {auth_err}")
        return jsonify({"success": False, "message": str(auth_err)}), 401

    data = request.json or {}
    required_fields = ['plan_id', 'scheduled_at']
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
            AND (role IN ('incoming_member', 'Incoming Team Member (Knowledge Receiver)', 'outgoing_sme', 'Outgoing SME (Knowledge Giver)'))
        """
        db_res = execute_query(query_roles, tuple(stakeholder_ids))
        valid_stakeholder_ids = [row['id'] for row in db_res]
        
        if len(valid_stakeholder_ids) != len(stakeholder_ids):
            return jsonify({"success": False, "message": "Invalid participants detected. Only Knowledge Givers and Receivers can be added."}), 400

        # Fetch plan topics
        topics_query = "SELECT day_label, topic_name FROM plan_topics WHERE plan_id = %s ORDER BY id ASC"
        topics_result = execute_query(topics_query, (data['plan_id'],))
        
        if not topics_result:
            return jsonify({"success": False, "message": "No topics found for this plan"}), 404
            
        from collections import defaultdict
        days_dict = defaultdict(list)
        for row in topics_result:
            days_dict[row['day_label']].append(row['topic_name'])
            
        # Prepare LLM prompt
        prompt = "I am scheduling a knowledge transfer series. Here are the topics grouped by days:\n\n"
        for day, topics in days_dict.items():
            prompt += f"{day}:\n"
            for t in topics:
                prompt += f"- {t}\n"
            prompt += "\n"
            
        prompt += """
Please generate a single Meeting Title and a brief Description for each day based on its topics. 
Return the output ONLY as a valid JSON array of objects. Each object must have three string keys: 'day', 'title', and 'description'.
For example:
[
  { "day": "Day 1: Python Fundamentals and Core Concepts", "title": "Day 1 KT: Python Fundamentals", "description": "Introduction, Setup, Syntax, and basic programming." }
]
"""
        from llm_service import call_llm
        import json
        llm_response = call_llm(prompt)
        try:
            if llm_response.startswith('```json'):
                llm_response = llm_response[7:-3]
            elif llm_response.startswith('```'):
                llm_response = llm_response[3:-3]
            day_details = json.loads(llm_response.strip())
        except Exception as parse_err:
            print(f"Error parsing LLM JSON: {parse_err}. Response: {llm_response}")
            return jsonify({"success": False, "message": "Failed to generate meeting schedule. Try again."}), 500

        from datetime import datetime, timedelta
        start_date_str = data['scheduled_at']
        try:
            if 'T' in start_date_str:
                current_dt = datetime.fromisoformat(start_date_str.replace('Z', '+00:00'))
            else:
                current_dt = datetime.strptime(start_date_str, '%Y-%m-%d')
        except ValueError:
            current_dt = datetime.strptime(start_date_str[:10], '%Y-%m-%d')
            
        current_dt = current_dt.replace(hour=10, minute=0, second=0, microsecond=0)
        
        meeting_ids = []
        for idx, details in enumerate(day_details):
            while current_dt.weekday() > 4: # Skip Sat/Sun
                current_dt += timedelta(days=1)
                
            formatted_date = current_dt.strftime('%Y-%m-%d %H:%M:%S')
            
            query = """
                INSERT INTO meetings (plan_id, title, scheduled_at, organizer_id, description, meeting_link)
                VALUES (%s, %s, %s, %s, %s, %s)
            """
            params = (
                data['plan_id'], 
                details.get('title', f'Meeting Day {idx+1}'), 
                formatted_date, 
                organizer_id,
                details.get('description', ''),
                data.get('meeting_link') or data.get('link')
            )
            meeting_id = execute_write(query, params)
            meeting_ids.append(meeting_id)
            
            for sh_id in valid_stakeholder_ids:
                try:
                    execute_write(
                        "INSERT INTO attendance (meeting_id, stakeholder_id) VALUES (%s, %s)",
                        (meeting_id, sh_id)
                    )
                except Exception as att_err:
                    print(f"Error inserting attendance for stakeholder {sh_id}: {att_err}")

            try:
                from services.notification_service import trigger_meeting_notifications
                trigger_meeting_notifications(meeting_id)
            except Exception as notify_err:
                print(f"Error triggering notifications: {notify_err}")

            current_dt += timedelta(days=1)

        return jsonify({
            "success": True, 
            "data": meeting_ids, 
            "message": "Meetings scheduled successfully. Email notifications initiated."
        }), 201
    except Exception as e:
        print(f"Error in create_meeting: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

@scheduling_bp.route('/meetings', methods=['GET'])
def get_meetings():
    plan_id = request.args.get('plan_id')
    try:
        try:
            user_info = get_authenticated_user()
            user_id = user_info['sub']
            user = execute_query("SELECT email, role FROM users WHERE id = %s", (user_id,))[0]
            user_email = user['email']
            user_role = user['role']
        except Exception as auth_err:
            return jsonify({"success": False, "message": str(auth_err)}), 401

        sh = execute_query("SELECT id FROM stakeholders WHERE email = %s", (user_email,))
        stakeholder_id = sh[0]['id'] if sh else None

        if user_role in ['Delivery / Engagement Manager', 'PwC Leadership']:
            base_query = "SELECT DISTINCT m.* FROM meetings m"
            params = []
            if plan_id:
                base_query += " WHERE m.plan_id = %s"
                params.append(plan_id)
        else:
            base_query = """
                SELECT DISTINCT m.* FROM meetings m 
                LEFT JOIN attendance a ON m.id = a.meeting_id 
                WHERE (m.organizer_id = %s OR a.stakeholder_id = %s)
            """
            params = [user_id, stakeholder_id]
            if plan_id:
                base_query += " AND m.plan_id = %s"
                params.append(plan_id)

        base_query += " ORDER BY m.scheduled_at ASC"
        meetings = execute_query(base_query, tuple(params))
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


@scheduling_bp.route('/meetings/<int:id>/reschedule', methods=['PUT'])
def reschedule_meeting(id):
    """
    Reschedules the time of an existing meeting (same date, new time).
    Accepts: { "new_time": "HH:MM", "reason": "optional reason" }
    Updates scheduled_at in DB and triggers reschedule email notifications.
    """
    try:
        user_info = get_authenticated_user()
        user_role = None
        try:
            uid = user_info['sub']
            u = execute_query("SELECT role FROM users WHERE id = %s", (uid,))
            if u:
                user_role = u[0]['role']
        except Exception:
            pass

        if user_role not in ['Delivery / Engagement Manager']:
            return jsonify({"success": False, "message": "Permission denied. Only Delivery / Engagement Managers can reschedule meetings."}), 403

    except Exception as auth_err:
        return jsonify({"success": False, "message": str(auth_err)}), 401

    data = request.json or {}
    new_time = data.get('new_time')  # Expected format: "HH:MM"
    reason = data.get('reason', '')

    if not new_time:
        return jsonify({"success": False, "message": "new_time is required (format HH:MM)"}), 400

    # Validate time format
    try:
        from datetime import datetime
        time_parts = new_time.split(':')
        if len(time_parts) < 2:
            raise ValueError("Invalid time")
        hour = int(time_parts[0])
        minute = int(time_parts[1])
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError("Time out of range")
    except (ValueError, IndexError):
        return jsonify({"success": False, "message": "Invalid new_time format. Use HH:MM (24-hour)."}), 400

    try:
        # Fetch the current scheduled_at
        meeting_row = execute_query("SELECT id, title, scheduled_at, status FROM meetings WHERE id = %s", (id,))
        if not meeting_row:
            return jsonify({"success": False, "message": "Meeting not found"}), 404

        meeting = meeting_row[0]
        if meeting['status'] != 'scheduled':
            return jsonify({"success": False, "message": "Only scheduled meetings can be rescheduled."}), 400

        # Parse existing date, replace only time
        existing_dt = meeting['scheduled_at']
        if isinstance(existing_dt, str):
            try:
                if 'T' in existing_dt:
                    existing_dt = datetime.strptime(existing_dt.replace('T', ' '), "%Y-%m-%d %H:%M:%S")
                else:
                    existing_dt = datetime.strptime(existing_dt, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                existing_dt = datetime.fromisoformat(existing_dt)

        # Keep the same date, change only the time
        new_dt = existing_dt.replace(hour=hour, minute=minute, second=0, microsecond=0)
        new_dt_str = new_dt.strftime('%Y-%m-%d %H:%M:%S')

        # Update DB
        execute_write(
            "UPDATE meetings SET scheduled_at = %s WHERE id = %s",
            (new_dt_str, id)
        )

        # Fire reschedule notifications in background
        try:
            from services.notification_service import trigger_reschedule_notifications
            trigger_reschedule_notifications(id, new_dt, reason)
        except Exception as notify_err:
            print(f"Error triggering reschedule notifications: {notify_err}")

        return jsonify({
            "success": True,
            "message": f"Meeting rescheduled to {new_dt_str}. Participants will be notified via email."
        }), 200

    except Exception as e:
        print(f"Error in reschedule_meeting: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

