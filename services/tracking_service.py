import re
from collections import defaultdict
from db import execute_query, execute_write

def get_topics_for_meeting(meeting_id):
    m_rows = execute_query("SELECT id, plan_id, title, scheduled_at, status FROM meetings WHERE id = %s", (meeting_id,))
    if not m_rows:
        return []
    meeting = m_rows[0]
    plan_id = meeting.get('plan_id')
    if not plan_id:
        return []

    # Get all meetings for this plan ordered by scheduled_at ASC, id ASC
    all_meetings = execute_query(
        "SELECT id, title, scheduled_at, status FROM meetings WHERE plan_id = %s ORDER BY scheduled_at ASC, id ASC",
        (plan_id,)
    )
    
    # Find position of this meeting among plan meetings (1-based: Day 1, Day 2, ...)
    meeting_pos = None
    for idx, m in enumerate(all_meetings):
        if m['id'] == meeting_id:
            meeting_pos = idx + 1
            break

    # Check if meeting title explicitly specifies Day X
    title = meeting.get('title') or ''
    title_day_match = re.search(r'\bDay\s*(\d+)\b', title, re.IGNORECASE)
    target_day_num = int(title_day_match.group(1)) if title_day_match else meeting_pos

    # Fetch all plan topics ordered by id
    all_topics = execute_query(
        "SELECT id, day_label, topic_name FROM plan_topics WHERE plan_id = %s ORDER BY id ASC",
        (plan_id,)
    )
    if not all_topics:
        return []

    # Filter out non-KT phase topics
    excluded_keywords = ['final assessment', 'shadow phase', 'lead phase', 'shadow support', 'lead support']
    filtered_topics = []
    for t in all_topics:
        lbl = f"{t.get('day_label') or ''} {t.get('topic_name') or ''}".lower()
        if not any(kw in lbl for kw in excluded_keywords):
            filtered_topics.append(t)

    # Group distinct day labels preserving order
    distinct_day_labels = []
    topics_by_day_label = defaultdict(list)
    for t in filtered_topics:
        dl = t.get('day_label') or ''
        if dl not in topics_by_day_label:
            distinct_day_labels.append(dl)
        topics_by_day_label[dl].append(t.get('topic_name'))

    matched_items = []

    # 1. Try matching by day number in day_label
    if target_day_num is not None:
        for dl, t_list in topics_by_day_label.items():
            dl_day_match = re.search(r'\bDay\s*(\d+)\b', dl, re.IGNORECASE)
            if dl_day_match and int(dl_day_match.group(1)) == target_day_num:
                for t in t_list:
                    matched_items.append({'topic': t, 'day_label': dl})

    # 2. If no match by day number, try matching by position in distinct_day_labels
    if not matched_items and meeting_pos is not None and 1 <= meeting_pos <= len(distinct_day_labels):
        pos_day_label = distinct_day_labels[meeting_pos - 1]
        for t in topics_by_day_label[pos_day_label]:
            matched_items.append({'topic': t, 'day_label': pos_day_label})

    # 3. Deduplicate preserving order
    seen = set()
    unique_items = []
    for item in matched_items:
        k = (item['topic'], item['day_label'])
        if k not in seen:
            seen.add(k)
            unique_items.append(item)

    return unique_items

def sync_meeting_topics_to_tracking(meeting_id, status=None):
    m_rows = execute_query("SELECT plan_id FROM meetings WHERE id = %s", (meeting_id,))
    if not m_rows:
        return
    plan_id = m_rows[0].get('plan_id')
    if not plan_id:
        return

    sync_plan_completion_from_schedule(plan_id)

def sync_plan_completion_from_schedule(plan_id):
    if not plan_id:
        return

    # Check if meetings exist for this plan
    all_meetings = execute_query(
        "SELECT id, status FROM meetings WHERE plan_id = %s ORDER BY scheduled_at ASC, id ASC",
        (plan_id,)
    )
    if not all_meetings:
        # No meetings exist for this plan, do not interfere with manual tracking
        return

    # Collect topics for all COMPLETED meetings (tracked individually per day)
    completed_items = []
    completed_keys = set()
    for m in all_meetings:
        if m['status'] == 'completed':
            m_items = get_topics_for_meeting(m['id'])
            for item in m_items:
                k = (item['topic'], item['day_label'])
                if k not in completed_keys:
                    completed_keys.add(k)
                    completed_items.append(item)

    # Clean up any legacy entries for this plan where day_label is NULL if we now have day-labeled entries
    if completed_items:
        execute_write("DELETE FROM completion_tracking WHERE plan_id = %s AND day_label IS NULL AND updated_by IS NULL", (plan_id,))

    # Mark completed topics in completion_tracking for each individual day
    for item in completed_items:
        execute_write(
            """INSERT INTO completion_tracking (plan_id, topic, day_label, completion_percent, updated_by)
               VALUES (%s, %s, %s, 100, NULL)
               ON DUPLICATE KEY UPDATE completion_percent = 100""",
            (plan_id, item['topic'], item['day_label'])
        )

    # Remove automatic entries for topics that are NOT completed
    current_tracking = execute_query(
        "SELECT id, topic, day_label, updated_by FROM completion_tracking WHERE plan_id = %s",
        (plan_id,)
    )
    for ct in current_tracking:
        k = (ct['topic'], ct['day_label'])
        if k not in completed_keys:
            # Only remove if it was automatically tracked (updated_by is NULL)
            if ct['updated_by'] is None:
                execute_write("DELETE FROM completion_tracking WHERE id = %s", (ct['id'],))

    # Check if plan is 100% completed and update unlocked_on in kt_plans
    try:
        comp_query = """
            SELECT 
                (SELECT COUNT(*) FROM completion_tracking 
                 WHERE plan_id = %s 
                 AND completion_percent = 100
                 AND LOWER(topic) NOT LIKE %s
                 AND LOWER(topic) NOT LIKE %s
                 AND LOWER(topic) NOT LIKE %s
                ) as completed_topics,
                (SELECT COUNT(*) FROM plan_topics 
                 WHERE plan_id = %s 
                 AND LOWER(CONCAT(IFNULL(day_label, ''), ' ', IFNULL(topic_name, ''))) NOT LIKE %s
                 AND LOWER(CONCAT(IFNULL(day_label, ''), ' ', IFNULL(topic_name, ''))) NOT LIKE %s
                 AND LOWER(CONCAT(IFNULL(day_label, ''), ' ', IFNULL(topic_name, ''))) NOT LIKE %s
                ) as total_topics
            FROM DUAL
        """
        comp_res = execute_query(comp_query, (
            plan_id, '%final assessment%', '%shadow phase%', '%lead phase%',
            plan_id, '%final assessment%', '%shadow phase%', '%lead phase%'
        ))
        if comp_res and comp_res[0].get('total_topics'):
            c_cnt = int(comp_res[0]['completed_topics'] or 0)
            t_cnt = int(comp_res[0]['total_topics'] or 0)
            if t_cnt > 0 and c_cnt >= t_cnt:
                execute_write("UPDATE kt_plans SET unlocked_on = COALESCE(unlocked_on, CURRENT_TIMESTAMP) WHERE id = %s", (plan_id,))
    except Exception:
        pass


def update_completion_service(plan_id, topic, completion_percent, updated_by=None, day_label=None):
    if not day_label:
        dl_row = execute_query("SELECT day_label FROM plan_topics WHERE plan_id = %s AND topic_name = %s LIMIT 1", (plan_id, topic))
        if dl_row and dl_row[0].get('day_label'):
            day_label = dl_row[0]['day_label']

    query = """
        INSERT INTO completion_tracking (plan_id, topic, day_label, completion_percent, updated_by)
        VALUES (%s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE completion_percent = VALUES(completion_percent), updated_by = VALUES(updated_by)
    """
    params = (plan_id, topic, day_label, completion_percent, updated_by)
    execute_write(query, params)

    # Check if plan is 100% completed and update unlocked_on in kt_plans
    try:
        comp_query = """
            SELECT 
                (SELECT COUNT(*) FROM completion_tracking 
                 WHERE plan_id = %s 
                 AND completion_percent = 100
                 AND LOWER(topic) NOT LIKE %s
                 AND LOWER(topic) NOT LIKE %s
                 AND LOWER(topic) NOT LIKE %s
                ) as completed_topics,
                (SELECT COUNT(*) FROM plan_topics 
                 WHERE plan_id = %s 
                 AND LOWER(CONCAT(IFNULL(day_label, ''), ' ', IFNULL(topic_name, ''))) NOT LIKE %s
                 AND LOWER(CONCAT(IFNULL(day_label, ''), ' ', IFNULL(topic_name, ''))) NOT LIKE %s
                 AND LOWER(CONCAT(IFNULL(day_label, ''), ' ', IFNULL(topic_name, ''))) NOT LIKE %s
                ) as total_topics
            FROM DUAL
        """
        comp_res = execute_query(comp_query, (
            plan_id, '%final assessment%', '%shadow phase%', '%lead phase%',
            plan_id, '%final assessment%', '%shadow phase%', '%lead phase%'
        ))
        if comp_res and comp_res[0].get('total_topics'):
            c_cnt = int(comp_res[0]['completed_topics'] or 0)
            t_cnt = int(comp_res[0]['total_topics'] or 0)
            if t_cnt > 0 and c_cnt >= t_cnt:
                execute_write("UPDATE kt_plans SET unlocked_on = COALESCE(unlocked_on, CURRENT_TIMESTAMP) WHERE id = %s", (plan_id,))
    except Exception:
        pass

def get_meeting_attendance_rate(meeting_id):
    query = """
        SELECT 
            COUNT(*) as total,
            SUM(CASE WHEN attended = 1 THEN 1 ELSE 0 END) as attended
        FROM attendance
        WHERE meeting_id = %s
    """
    res = execute_query(query, (meeting_id,))
    total = int(res[0]['total']) if res and res[0]['total'] else 0
    attended = int(res[0]['attended']) if res and res[0]['attended'] else 0
    rate = (attended / total * 100) if total > 0 else 0.0
    return round(rate, 2)

def get_plan_summary_service(plan_id):
    try:
        sync_plan_completion_from_schedule(plan_id)
    except Exception as e:
        print(f"Error syncing schedule in get_plan_summary_service: {e}")

    # Get average completion
    comp_query = """
        SELECT 
            (SELECT COUNT(*) FROM completion_tracking 
             WHERE plan_id = %s 
             AND completion_percent = 100
             AND LOWER(topic) NOT LIKE %s
             AND LOWER(topic) NOT LIKE %s
             AND LOWER(topic) NOT LIKE %s
            ) as completed_topics,
            (SELECT COUNT(*) FROM plan_topics 
             WHERE plan_id = %s 
             AND LOWER(CONCAT(IFNULL(day_label, ''), ' ', IFNULL(topic_name, ''))) NOT LIKE %s
             AND LOWER(CONCAT(IFNULL(day_label, ''), ' ', IFNULL(topic_name, ''))) NOT LIKE %s
             AND LOWER(CONCAT(IFNULL(day_label, ''), ' ', IFNULL(topic_name, ''))) NOT LIKE %s
            ) as total_topics
        FROM DUAL
    """
    comp_res = execute_query(comp_query, (
        plan_id, '%final assessment%', '%shadow phase%', '%lead phase%',
        plan_id, '%final assessment%', '%shadow phase%', '%lead phase%'
    ))
    
    if comp_res and comp_res[0]['total_topics'] and int(comp_res[0]['total_topics']) > 0:
        avg_completion = (float(comp_res[0]['completed_topics']) / float(comp_res[0]['total_topics'])) * 100.0
    else:
        avg_completion = 0.0
    
    # Calculate overall attendance rate as the average of the individual meeting attendance rates
    meetings_query = "SELECT id FROM meetings WHERE plan_id = %s"
    meetings = execute_query(meetings_query, (plan_id,))
    if meetings:
        total_rates = 0.0
        for m in meetings:
            total_rates += get_meeting_attendance_rate(m['id'])
        avg_attendance_rate = total_rates / len(meetings)
    else:
        avg_attendance_rate = 0.0
        
    return {
        "avg_completion_percent": round(avg_completion, 2),
        "attendance_rate_percent": round(avg_attendance_rate, 2)
    }
