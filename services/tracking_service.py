from db import execute_query, execute_write

def update_completion_service(plan_id, topic, completion_percent):
    query = """
        INSERT INTO completion_tracking (plan_id, topic, completion_percent)
        VALUES (%s, %s, %s)
        ON DUPLICATE KEY UPDATE completion_percent = VALUES(completion_percent)
    """
    params = (plan_id, topic, completion_percent)
    execute_write(query, params)

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
    # Get average completion
    comp_query = "SELECT AVG(completion_percent) as avg_completion FROM completion_tracking WHERE plan_id = %s"
    comp_res = execute_query(comp_query, (plan_id,))
    avg_completion = float(comp_res[0]['avg_completion']) if comp_res and comp_res[0]['avg_completion'] else 0
    
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
