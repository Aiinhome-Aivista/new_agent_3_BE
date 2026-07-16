from db import execute_query, execute_write

def update_completion_service(plan_id, topic, completion_percent):
    query = """
        INSERT INTO completion_tracking (plan_id, topic, completion_percent)
        VALUES (%s, %s, %s)
        ON DUPLICATE KEY UPDATE completion_percent = VALUES(completion_percent)
    """
    params = (plan_id, topic, completion_percent)
    execute_write(query, params)

def get_plan_summary_service(plan_id):
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
    
    return {
        "avg_completion_percent": round(avg_completion, 2),
        "attendance_rate_percent": round(attendance_rate, 2)
    }
