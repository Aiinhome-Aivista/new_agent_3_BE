from db import execute_query
from services.tracking_service import get_plan_summary_service

def get_manager_wise_summary():
    plans_query = """
        SELECT kp.id as plan_id, kp.application_name, kp.status, kp.approved_by, s.name as manager_name
        FROM kt_plans kp
        LEFT JOIN stakeholders s ON kp.approved_by = s.id
    """
    plans = execute_query(plans_query)

    managers = {}
    for p in plans:
        manager_key = p['manager_name'] or 'Unassigned'
        if manager_key not in managers:
            managers[manager_key] = {"manager_name": manager_key, "plans": []}

        # Reuse the EXACT SAME calculation the Tracking page uses — guarantees the numbers always match
        plan_summary = get_plan_summary_service(p['plan_id'])
        plan_completion = plan_summary['avg_completion_percent']

        # Topic count for this plan, used to weight the manager-level aggregate
        topic_count_query = "SELECT COUNT(*) as cnt FROM plan_topics WHERE plan_id = %s"
        topic_count_res = execute_query(topic_count_query, (p['plan_id'],))
        topic_count = int(topic_count_res[0]['cnt']) if topic_count_res and topic_count_res[0]['cnt'] else 0

        managers[manager_key]["plans"].append({
            "plan_id": p['plan_id'],
            "application_name": p['application_name'],
            "status": p['status'],
            "completion_percent": plan_completion,
            "topic_count": topic_count
        })

    result = []
    for m in managers.values():
        plans_list = m["plans"]
        total_weight = sum(pl['topic_count'] for pl in plans_list)

        if total_weight > 0:
            weighted_sum = sum(pl['completion_percent'] * pl['topic_count'] for pl in plans_list)
            overall = round(weighted_sum / total_weight, 2)
        elif plans_list:
            # Fallback if no plan has any topics defined yet: simple average
            overall = round(sum(pl['completion_percent'] for pl in plans_list) / len(plans_list), 2)
        else:
            overall = 0.0

        result.append({
            "manager_name": m["manager_name"],
            "total_plans": len(plans_list),
            "overall_completion_percent": overall,
            "plans": plans_list
        })

    all_plan_completions = [pl['completion_percent'] for m in result for pl in m['plans']]
    combined_avg = round(sum(all_plan_completions) / len(all_plan_completions), 2) if all_plan_completions else 0.0

    return {
        "managers": result,
        "combined_average_completion_percent": combined_avg,
        "total_managers": len(result)
    }

def get_manager_wise_risk_summary():
    plans_query = """
        SELECT kp.id as plan_id, kp.application_name, kp.status, s.name as manager_name
        FROM kt_plans kp
        LEFT JOIN stakeholders s ON kp.approved_by = s.id
    """
    plans = execute_query(plans_query)

    risks_query = "SELECT id, plan_id, description, severity, status, created_at FROM risks"
    all_risks = execute_query(risks_query)

    for r in all_risks:
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

    managers = {}
    for p in plans:
        manager_key = p['manager_name'] or 'Unassigned'
        if manager_key not in managers:
            managers[manager_key] = {"manager_name": manager_key, "plans": []}

        plan_risks = [r for r in all_risks if r['plan_id'] == p['plan_id']]
        
        severity_counts = {'low': 0, 'medium': 0, 'high': 0, 'critical': 0, 'solved': 0, 'in_progress': 0, 'waiting_for_approval': 0}
        for r in plan_risks:
            if r['status'] == 'solved' or r['status'] == 'resolved':
                severity_counts['solved'] += 1
            elif r['status'] == 'in_progress':
                severity_counts['in_progress'] += 1
            elif r['status'] == 'waiting_for_approval':
                severity_counts['waiting_for_approval'] += 1
            else:
                sev = r['severity'].lower()
                if sev in severity_counts:
                    severity_counts[sev] += 1
                
        open_plan_risks = [r for r in plan_risks if r['status'] not in ('solved', 'resolved')]
        managers[manager_key]["plans"].append({
            "plan_id": p['plan_id'],
            "application_name": p['application_name'],
            "status": p['status'],
            "total_risks": len(plan_risks),
            "open_risks": len(open_plan_risks),
            "severity_counts": severity_counts,
            "risks": plan_risks
        })

    result = []
    total_risks_all = 0
    total_open_risks_all = 0
    
    for m in managers.values():
        plans_list = m["plans"]
        m_total_risks = sum(pl['total_risks'] for pl in plans_list)
        m_open_risks = sum(pl['open_risks'] for pl in plans_list)
        total_risks_all += m_total_risks
        total_open_risks_all += m_open_risks
        
        m_severity_counts = {'low': 0, 'medium': 0, 'high': 0, 'critical': 0, 'solved': 0, 'in_progress': 0, 'waiting_for_approval': 0}
        for pl in plans_list:
            for sev, count in pl['severity_counts'].items():
                m_severity_counts[sev] += count

        result.append({
            "manager_name": m["manager_name"],
            "total_plans": len(plans_list),
            "total_risks": m_total_risks,
            "open_risks": m_open_risks,
            "severity_counts": m_severity_counts,
            "plans": plans_list
        })

    return {
        "managers": result,
        "total_risks": total_risks_all,
        "total_open_risks": total_open_risks_all,
        "total_managers": len(result)
    }

