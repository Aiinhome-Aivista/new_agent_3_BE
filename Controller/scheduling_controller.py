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
Please generate a single Meeting Title, a brief Description, and a random start_time for each day based on its topics.
The start_time must be chosen randomly within working hours: between 10:00 and 17:00 (24-hour format, HH:MM).
Each day should ideally have a DIFFERENT start_time — vary them naturally (e.g., 10:30, 14:00, 11:45, 15:30, etc.).
Meetings are 2 hours long, so the latest start time allowed is 17:00 so the session ends by 19:00.
Return the output ONLY as a valid JSON array of objects. Each object must have exactly four string keys: 'day', 'title', 'description', and 'start_time'.
For example:
[
  { "day": "Day 1: Python Fundamentals and Core Concepts", "title": "Day 1 KT: Python Fundamentals", "description": "Introduction, Setup, Syntax, and basic programming.", "start_time": "10:30" },
  { "day": "Day 2: Advanced Python", "title": "Day 2 KT: Advanced Python", "description": "OOP, decorators, generators.", "start_time": "14:00" }
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
        import random
        start_date_str = data['scheduled_at']
        try:
            if 'T' in start_date_str:
                base_dt = datetime.fromisoformat(start_date_str.replace('Z', '+00:00'))
            else:
                base_dt = datetime.strptime(start_date_str, '%Y-%m-%d')
        except ValueError:
            base_dt = datetime.strptime(start_date_str[:10], '%Y-%m-%d')

        # Use start_date as the rolling day pointer (time is set per-meeting below)
        current_day = base_dt.replace(hour=0, minute=0, second=0, microsecond=0)

        meeting_ids = []
        for idx, details in enumerate(day_details):
            # Parse LLM-chosen start_time (HH:MM); fall back to a random time if invalid
            raw_time = details.get('start_time', '')
            try:
                t_parts = raw_time.strip().split(':')
                desired_hour = int(t_parts[0])
                desired_minute = int(t_parts[1]) if len(t_parts) > 1 else 0
                if not (10 <= desired_hour <= 17):
                    raise ValueError("out of range")
                if desired_hour == 17 and desired_minute > 0:
                    desired_minute = 0
            except Exception:
                desired_hour = random.randint(10, 17)
                desired_minute = random.choice([0, 15, 30, 45])
                if desired_hour == 17:
                    desired_minute = 0

            desired_start = desired_hour * 60 + desired_minute

            scheduled = False
            days_checked = 0
            while not scheduled and days_checked < 30:
                while current_day.weekday() > 4:  # Skip Sat/Sun
                    current_day += timedelta(days=1)

                day_start = current_day.strftime('%Y-%m-%d 00:00:00')
                day_end = (current_day + timedelta(days=1)).strftime('%Y-%m-%d 00:00:00')
                format_strings_sh = ','.join(['%s'] * len(valid_stakeholder_ids))
                
                existing_query = f"""
                    SELECT m.scheduled_at 
                    FROM meetings m
                    JOIN attendance a ON m.id = a.meeting_id
                    WHERE a.stakeholder_id IN ({format_strings_sh})
                      AND m.scheduled_at >= %s AND m.scheduled_at < %s
                      AND m.status = 'scheduled'
                """
                params = tuple(valid_stakeholder_ids) + (day_start, day_end)
                try:
                    existing_meetings = execute_query(existing_query, params)
                except Exception:
                    existing_query_fb = f"""
                        SELECT m.scheduled_at 
                        FROM meetings m
                        JOIN attendance a ON m.id = a.meeting_id
                        WHERE a.stakeholder_id IN ({format_strings_sh})
                          AND m.scheduled_at >= %s AND m.scheduled_at < %s
                    """
                    existing_meetings = execute_query(existing_query_fb, params)

                existing_starts = []
                if existing_meetings:
                    for row in existing_meetings:
                        dt = row['scheduled_at']
                        if isinstance(dt, str):
                            try:
                                if 'T' in dt:
                                    dt = datetime.strptime(dt.replace('T', ' '), "%Y-%m-%d %H:%M:%S")
                                else:
                                    dt = datetime.strptime(dt, "%Y-%m-%d %H:%M:%S")
                            except ValueError:
                                dt = datetime.fromisoformat(dt)
                        existing_starts.append(dt.hour * 60 + dt.minute)

                has_overlap = False
                for ext_start in existing_starts:
                    if abs(desired_start - ext_start) < 120:
                        has_overlap = True
                        break

                if not has_overlap:
                    final_start = desired_start
                    scheduled = True
                else:
                    found_slot = False
                    for slot in range(600, 1021, 15):
                        slot_overlap = False
                        for ext_start in existing_starts:
                            if abs(slot - ext_start) < 120:
                                slot_overlap = True
                                break
                        if not slot_overlap:
                            final_start = slot
                            scheduled = True
                            found_slot = True
                            break
                    
                    if not found_slot:
                        current_day += timedelta(days=1)
                        days_checked += 1

            if not scheduled:
                raise Exception("Could not find an available time slot for the participants.")

            hour = final_start // 60
            minute = final_start % 60
            current_dt = current_day.replace(hour=hour, minute=minute, second=0, microsecond=0)
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

            # Advance to the next calendar day (time will be set fresh in next iteration)
            current_day += timedelta(days=1)

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
            base_query = "SELECT DISTINCT m.*, p.application_name as plan_name FROM meetings m JOIN kt_plans p ON m.plan_id = p.id"
            params = []
            if plan_id:
                base_query += " WHERE m.plan_id = %s"
                params.append(plan_id)
        else:
            base_query = """
                SELECT DISTINCT m.*, p.application_name as plan_name, a.attended
                FROM meetings m 
                JOIN kt_plans p ON m.plan_id = p.id
                LEFT JOIN attendance a ON m.id = a.meeting_id AND a.stakeholder_id = %s
                WHERE (m.organizer_id = %s OR a.stakeholder_id = %s)
            """
            params = [stakeholder_id, user_id, stakeholder_id]
            if plan_id:
                base_query += " AND m.plan_id = %s"
                params.append(plan_id)

        base_query += " ORDER BY m.scheduled_at ASC"
        meetings = execute_query(base_query, tuple(params))
        from services.tracking_service import get_meeting_attendance_rate
        
        plan_day_counters = {}
        for m in meetings:
            m['attendance_rate_percent'] = get_meeting_attendance_rate(m['id'])
            pid = m['plan_id']
            if pid not in plan_day_counters:
                plan_day_counters[pid] = 1
            m['day_label'] = f"Day {plan_day_counters[pid]}"
            plan_day_counters[pid] += 1
            
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
    Reschedules the date and time of an existing meeting.
    Accepts: { "new_time": "HH:MM", "new_date": "YYYY-MM-DD", "reason": "optional reason" }
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
    new_date = data.get('new_date')  # Expected format: "YYYY-MM-DD"
    reason = data.get('reason', '')

    if not new_time:
        return jsonify({"success": False, "message": "new_time is required (format HH:MM)"}), 400

    if new_date:
        from datetime import datetime
        try:
            datetime.strptime(new_date, '%Y-%m-%d')
        except ValueError:
            return jsonify({"success": False, "message": "Invalid new_date format. Use YYYY-MM-DD."}), 400

    # Validate time format
    try:
        from datetime import datetime, timedelta
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
        meeting_row = execute_query("SELECT id, plan_id, title, scheduled_at, status FROM meetings WHERE id = %s", (id,))
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

        # Update date if provided, change time
        if new_date:
            date_parts = new_date.split('-')
            new_dt = existing_dt.replace(year=int(date_parts[0]), month=int(date_parts[1]), day=int(date_parts[2]), hour=hour, minute=minute, second=0, microsecond=0)
        else:
            new_dt = existing_dt.replace(hour=hour, minute=minute, second=0, microsecond=0)
            
        if new_dt.weekday() > 4:
            return jsonify({"success": False, "message": "Meetings cannot be rescheduled to a weekend (Saturday or Sunday)."}), 400
            
        new_dt_str = new_dt.strftime('%Y-%m-%d %H:%M:%S')

        reschedule_subsequent = data.get('reschedule_subsequent', False)
        
        proposed_meetings = [(id, new_dt)]
        subsequent_meetings = []
        plan_id = meeting['plan_id']
        
        if reschedule_subsequent:
            biz_days_shift = 0
            temp_date = existing_dt.replace(hour=0, minute=0, second=0, microsecond=0)
            end_date_only = new_dt.replace(hour=0, minute=0, second=0, microsecond=0)
            
            if end_date_only > temp_date:
                while temp_date < end_date_only:
                    temp_date += timedelta(days=1)
                    if temp_date.weekday() <= 4:
                        biz_days_shift += 1
            elif end_date_only < temp_date:
                while temp_date > end_date_only:
                    temp_date -= timedelta(days=1)
                    if temp_date.weekday() <= 4:
                        biz_days_shift -= 1
                        
            dt_same_day = existing_dt.replace(year=new_dt.year, month=new_dt.month, day=new_dt.day)
            time_of_day_delta = new_dt - dt_same_day
            
            subsequent_meetings = execute_query(
                "SELECT id, scheduled_at FROM meetings WHERE plan_id = %s AND scheduled_at > %s AND status = 'scheduled' ORDER BY scheduled_at ASC",
                (plan_id, existing_dt.strftime('%Y-%m-%d %H:%M:%S'))
            )
            
            for sub_m in subsequent_meetings:
                sub_existing_dt = sub_m['scheduled_at']
                if isinstance(sub_existing_dt, str):
                    try:
                        if 'T' in sub_existing_dt:
                            sub_existing_dt = datetime.strptime(sub_existing_dt.replace('T', ' '), "%Y-%m-%d %H:%M:%S")
                        else:
                            sub_existing_dt = datetime.strptime(sub_existing_dt, "%Y-%m-%d %H:%M:%S")
                    except ValueError:
                        sub_existing_dt = datetime.fromisoformat(sub_existing_dt)
                
                sub_new_dt = sub_existing_dt
                shifts_left = biz_days_shift
                if shifts_left > 0:
                    while shifts_left > 0:
                        sub_new_dt += timedelta(days=1)
                        if sub_new_dt.weekday() <= 4:
                            shifts_left -= 1
                elif shifts_left < 0:
                    while shifts_left < 0:
                        sub_new_dt -= timedelta(days=1)
                        if sub_new_dt.weekday() <= 4:
                            shifts_left += 1
                            
                sub_new_dt += time_of_day_delta
                
                while sub_new_dt.weekday() > 4:
                    sub_new_dt += timedelta(days=1)
                
                proposed_meetings.append((sub_m['id'], sub_new_dt))

        # Check for overlaps
        att_rows = execute_query("SELECT stakeholder_id FROM attendance WHERE meeting_id = %s", (id,))
        stakeholders = [r['stakeholder_id'] for r in att_rows]
        
        final_proposed_meetings = []
        time_was_adjusted = False

        if stakeholders:
            format_strings_sh = ','.join(['%s'] * len(stakeholders))
            prop_ids = [m[0] for m in proposed_meetings]
            format_strings_prop = ','.join(['%s'] * len(prop_ids))
            
            assigned_slots_by_date = {}

            for m_id, prop_dt in proposed_meetings:
                current_day = prop_dt.replace(hour=0, minute=0, second=0, microsecond=0)
                desired_start = prop_dt.hour * 60 + prop_dt.minute
                
                scheduled = False
                days_checked = 0
                
                while not scheduled and days_checked < 30:
                    while current_day.weekday() > 4:  # Skip Sat/Sun
                        current_day += timedelta(days=1)
                        time_was_adjusted = True

                    day_start_str = current_day.strftime('%Y-%m-%d 00:00:00')
                    day_end = current_day + timedelta(days=1)
                    day_end_str = day_end.strftime('%Y-%m-%d 00:00:00')
                    
                    existing_query = f"""
                        SELECT m.scheduled_at 
                        FROM meetings m
                        JOIN attendance a ON m.id = a.meeting_id
                        WHERE a.stakeholder_id IN ({format_strings_sh})
                          AND m.id NOT IN ({format_strings_prop})
                          AND m.scheduled_at >= %s AND m.scheduled_at < %s
                          AND m.status = 'scheduled'
                    """
                    params = tuple(stakeholders) + tuple(prop_ids) + (day_start_str, day_end_str)
                    try:
                        existing_meetings = execute_query(existing_query, params)
                    except Exception:
                        existing_query_fb = f"""
                            SELECT m.scheduled_at 
                            FROM meetings m
                            JOIN attendance a ON m.id = a.meeting_id
                            WHERE a.stakeholder_id IN ({format_strings_sh})
                              AND m.id NOT IN ({format_strings_prop})
                              AND m.scheduled_at >= %s AND m.scheduled_at < %s
                        """
                        existing_meetings = execute_query(existing_query_fb, params)

                    existing_starts = []
                    if existing_meetings:
                        for row in existing_meetings:
                            ext_dt = row['scheduled_at']
                            if isinstance(ext_dt, str):
                                try:
                                    if 'T' in ext_dt:
                                        ext_dt = datetime.strptime(ext_dt.replace('T', ' '), "%Y-%m-%d %H:%M:%S")
                                    else:
                                        ext_dt = datetime.strptime(ext_dt, "%Y-%m-%d %H:%M:%S")
                                except ValueError:
                                    ext_dt = datetime.fromisoformat(ext_dt)
                            
                            existing_starts.append(ext_dt.hour * 60 + ext_dt.minute)
                            
                    date_key = current_day.strftime('%Y-%m-%d')
                    if date_key in assigned_slots_by_date:
                        existing_starts.extend(assigned_slots_by_date[date_key])

                    has_overlap = False
                    for ext_start in existing_starts:
                        if abs(desired_start - ext_start) < 120:
                            has_overlap = True
                            break

                    if not has_overlap:
                        final_start = desired_start
                        scheduled = True
                    else:
                        found_slot = False
                        for slot in range(600, 1021, 15):
                            slot_overlap = False
                            for ext_start in existing_starts:
                                if abs(slot - ext_start) < 120:
                                    slot_overlap = True
                                    break
                            if not slot_overlap:
                                final_start = slot
                                scheduled = True
                                found_slot = True
                                time_was_adjusted = True
                                break
                        
                        if not found_slot:
                            current_day += timedelta(days=1)
                            days_checked += 1
                            time_was_adjusted = True
                            desired_start = 600 # default to 10:00 on the next day if we must advance

                if not scheduled:
                    return jsonify({"success": False, "message": "Could not find a free slot to reschedule the meetings without overlaps."}), 400

                hour = final_start // 60
                minute = final_start % 60
                final_dt = current_day.replace(hour=hour, minute=minute, second=0, microsecond=0)
                final_proposed_meetings.append((m_id, final_dt))
                
                final_date_key = current_day.strftime('%Y-%m-%d')
                if final_date_key not in assigned_slots_by_date:
                    assigned_slots_by_date[final_date_key] = []
                assigned_slots_by_date[final_date_key].append(final_start)
        else:
            final_proposed_meetings = proposed_meetings
            time_was_adjusted = False

        # No overlaps found, safe to update database
        for m_id, prop_dt in final_proposed_meetings:
            execute_write(
                "UPDATE meetings SET scheduled_at = %s WHERE id = %s",
                (prop_dt.strftime('%Y-%m-%d %H:%M:%S'), m_id)
            )

        if reschedule_subsequent:
            try:
                from services.notification_service import trigger_reschedule_notifications
                for m_id, prop_dt in final_proposed_meetings:
                    trigger_reschedule_notifications(m_id, prop_dt, reason)
            except Exception as notify_err:
                print(f"Error triggering reschedule notifications: {notify_err}")
            msg = f"Meeting and {len(subsequent_meetings)} subsequent meetings rescheduled successfully. Participants will be notified via email."
            if time_was_adjusted:
                msg = "Rescheduled successfully. Some meeting times were adjusted to prevent overlaps. Participants notified."
        else:
            final_dt = final_proposed_meetings[0][1]
            try:
                from services.notification_service import trigger_reschedule_notifications
                trigger_reschedule_notifications(id, final_dt, reason)
            except Exception as notify_err:
                print(f"Error triggering reschedule notifications: {notify_err}")
            msg = f"Meeting rescheduled to {final_dt.strftime('%Y-%m-%d %H:%M')}. Participants will be notified via email."
            if time_was_adjusted:
                msg = f"Meeting time adjusted to {final_dt.strftime('%Y-%m-%d %H:%M')} to prevent overlap. Participants notified."

        return jsonify({
            "success": True,
            "message": msg
        }), 200

    except Exception as e:
        print(f"Error in reschedule_meeting: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


def init_feedback_table():
    try:
        execute_write('''
            CREATE TABLE IF NOT EXISTS meeting_feedback (
                id INT AUTO_INCREMENT PRIMARY KEY,
                meeting_id INT NOT NULL,
                plan_id INT NULL,
                knowledge_giver_id INT NOT NULL,
                knowledge_receiver_id INT NOT NULL,
                rating INT NOT NULL CHECK (rating >= 1 AND rating <= 5),
                feedback_text TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                FOREIGN KEY (meeting_id) REFERENCES meetings(id) ON DELETE CASCADE,
                FOREIGN KEY (knowledge_giver_id) REFERENCES stakeholders(id) ON DELETE CASCADE,
                FOREIGN KEY (knowledge_receiver_id) REFERENCES stakeholders(id) ON DELETE CASCADE,
                UNIQUE KEY idx_meeting_giver_receiver (meeting_id, knowledge_giver_id, knowledge_receiver_id)
            );
        ''')
    except Exception as err:
        print(f"Error initializing meeting_feedback table: {err}")

@scheduling_bp.route('/meetings/<int:id>/feedback', methods=['GET'])
def get_meeting_feedback(id):
    init_feedback_table()
    try:
        user_info = get_authenticated_user()
        user_id = user_info['sub']
        user = execute_query("SELECT email, role FROM users WHERE id = %s", (user_id,))
        if not user:
            return jsonify({"success": False, "message": "User not found"}), 404
        user_email = user[0]['email']

        sh = execute_query("SELECT id, name, role FROM stakeholders WHERE email = %s", (user_email,))
        receiver_id = sh[0]['id'] if sh else None

        # Fetch meeting info
        meeting = execute_query("SELECT id, plan_id, title FROM meetings WHERE id = %s", (id,))
        if not meeting:
            return jsonify({"success": False, "message": "Meeting not found"}), 404
        
        meeting_obj = meeting[0]

        # Find knowledge givers for this meeting
        givers_query = """
            SELECT DISTINCT s.id, s.name, s.email, s.role
            FROM stakeholders s
            JOIN attendance a ON a.stakeholder_id = s.id
            WHERE a.meeting_id = %s AND (s.role IN ('outgoing_sme', 'Outgoing SME (Knowledge Giver)'))
        """
        givers = execute_query(givers_query, (id,))

        organizer_query = """
            SELECT DISTINCT s.id, s.name, s.email, s.role
            FROM stakeholders s
            JOIN users u ON u.email = s.email
            JOIN meetings m ON m.organizer_id = u.id
            WHERE m.id = %s AND (s.role IN ('outgoing_sme', 'Outgoing SME (Knowledge Giver)') OR u.role = 'Outgoing SME (Knowledge Giver)')
        """
        organizer_givers = execute_query(organizer_query, (id,))

        giver_dict = {}
        for g in (givers + organizer_givers):
            giver_dict[g['id']] = g

        if not giver_dict:
            all_givers = execute_query("SELECT id, name, email, role FROM stakeholders WHERE role IN ('outgoing_sme', 'Outgoing SME (Knowledge Giver)')")
            for g in all_givers:
                giver_dict[g['id']] = g

        giver_list = list(giver_dict.values())

        existing_feedback_dict = {}
        if receiver_id:
            fb_rows = execute_query(
                "SELECT knowledge_giver_id, rating, feedback_text FROM meeting_feedback WHERE meeting_id = %s AND knowledge_receiver_id = %s",
                (id, receiver_id)
            )
            for row in fb_rows:
                existing_feedback_dict[row['knowledge_giver_id']] = row

        result_givers = []
        for g in giver_list:
            fb = existing_feedback_dict.get(g['id'], {})
            result_givers.append({
                "id": g['id'],
                "name": g['name'],
                "email": g['email'],
                "role": g['role'],
                "rating": fb.get('rating', 0),
                "feedback_text": fb.get('feedback_text', '')
            })

        return jsonify({
            "success": True,
            "meeting": meeting_obj,
            "givers": result_givers,
            "receiver_id": receiver_id
        }), 200
    except Exception as e:
        print(f"Error in get_meeting_feedback: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


@scheduling_bp.route('/meetings/<int:id>/feedback', methods=['POST'])
def submit_meeting_feedback(id):
    init_feedback_table()
    try:
        user_info = get_authenticated_user()
        user_id = user_info['sub']
        user = execute_query("SELECT email, role FROM users WHERE id = %s", (user_id,))
        if not user:
            return jsonify({"success": False, "message": "User not found"}), 404
        user_email = user[0]['email']
        user_role = user[0]['role']

        sh = execute_query("SELECT id FROM stakeholders WHERE email = %s", (user_email,))
        if not sh:
            return jsonify({"success": False, "message": "Stakeholder profile not found for user"}), 404
        receiver_id = sh[0]['id']

        meeting = execute_query("SELECT plan_id FROM meetings WHERE id = %s", (id,))
        if not meeting:
            return jsonify({"success": False, "message": "Meeting not found"}), 404
        plan_id = meeting[0]['plan_id']

        data = request.json or {}
        feedbacks = data.get('feedbacks', [])
        if not feedbacks:
            return jsonify({"success": False, "message": "No feedback provided."}), 400

        for item in feedbacks:
            giver_id = item.get('knowledge_giver_id')
            rating = item.get('rating')
            feedback_text = item.get('feedback_text', '')

            if not giver_id or not rating or rating < 1 or rating > 5:
                continue

            sql = """
                INSERT INTO meeting_feedback (meeting_id, plan_id, knowledge_giver_id, knowledge_receiver_id, rating, feedback_text)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE rating = VALUES(rating), feedback_text = VALUES(feedback_text)
            """
            execute_write(sql, (id, plan_id, giver_id, receiver_id, rating, feedback_text))

        return jsonify({"success": True, "message": "Feedback and ratings submitted successfully!"}), 200
    except Exception as e:
        print(f"Error in submit_meeting_feedback: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


