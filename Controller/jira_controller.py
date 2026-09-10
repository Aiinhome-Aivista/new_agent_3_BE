from flask import Blueprint, request, jsonify
from services.jira_service import JiraService
from db import execute_query, execute_write
from config import Config
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)

jira_bp = Blueprint('jira_bp', __name__)

@jira_bp.route('/connect', methods=['POST'])
def connect_jira():
    data = request.json or {}
    domain_url = data.get('domainUrl') or data.get('jira_url') or Config.JIRA_BASE_URL
    email = data.get('email')
    api_token = data.get('apiToken') or data.get('api_token')

    if not email or not api_token:
        return jsonify({
            "success": False,
            "message": "Jira Email and API Token are required."
        }), 400

    # 1. Test Jira connection
    auth_res = JiraService.test_connection(domain_url, email, api_token)
    if not auth_res.get('success'):
        return jsonify(auth_res), 400

    # 2. Fetch projects
    proj_res = JiraService.get_projects(domain_url, email, api_token)

    return jsonify({
        "success": True,
        "message": "Connected to Jira successfully!",
        "user": auth_res.get('user'),
        "projects": proj_res.get('projects', []),
        "domainUrl": JiraService._clean_url(domain_url)
    })

@jira_bp.route('/projects', methods=['POST'])
def get_projects():
    data = request.json or {}
    domain_url = data.get('domainUrl') or Config.JIRA_BASE_URL
    email = data.get('email')
    api_token = data.get('apiToken')

    if not email or not api_token:
        return jsonify({"success": False, "message": "Email and API Token are required"}), 400

    res = JiraService.get_projects(domain_url, email, api_token)
    return jsonify(res)

@jira_bp.route('/tickets', methods=['POST'])
def fetch_tickets():
    data = request.json or {}
    domain_url = data.get('domainUrl') or Config.JIRA_BASE_URL
    email = data.get('email')
    api_token = data.get('apiToken')
    project_key = data.get('projectKey')
    jql = data.get('jql')

    if not email or not api_token:
        return jsonify({"success": False, "message": "Email and API Token are required"}), 400

    res = JiraService.fetch_issues(
        domain_url=domain_url,
        email=email,
        api_token=api_token,
        project_key=project_key,
        jql=jql
    )
    return jsonify(res)

@jira_bp.route('/import-schedule', methods=['POST'])
def import_tickets_to_schedule():
    data = request.json or {}
    plan_id = data.get('plan_id')
    tickets = data.get('tickets') or []
    scheduled_date_str = data.get('scheduled_at') or datetime.now().strftime('%Y-%m-%d 10:00:00')
    domain_url = data.get('domainUrl') or Config.JIRA_BASE_URL

    if not plan_id:
        return jsonify({"success": False, "message": "plan_id is required"}), 400

    if not tickets:
        return jsonify({"success": False, "message": "No tickets provided to import"}), 400

    try:
        # Determine base datetime
        if 'T' in scheduled_date_str:
            base_dt = datetime.fromisoformat(scheduled_date_str.replace('Z', '+00:00'))
        else:
            base_dt = datetime.strptime(scheduled_date_str[:10], '%Y-%m-%d')
    except Exception:
        base_dt = datetime.now() + timedelta(days=1)

    imported_meetings = []
    current_dt = base_dt.replace(hour=10, minute=0, second=0, microsecond=0)
    clean_domain = JiraService._clean_url(domain_url)

    for i, ticket in enumerate(tickets):
        ticket_key = ticket.get('key', '')
        summary = ticket.get('summary', 'Jira Issue')
        desc = ticket.get('description', '')
        status = ticket.get('status', '')
        assignee = ticket.get('assignee', '')

        title = f"[{ticket_key}] {summary}" if ticket_key else summary
        full_desc = f"Imported from Jira ({ticket_key})\nStatus: {status}\nAssignee: {assignee}\n\nDescription:\n{desc}"

        meeting_time = (current_dt + timedelta(hours=i)).strftime('%Y-%m-%d %H:%M:%S')
        jira_link = f"{clean_domain}/browse/{ticket_key}" if clean_domain and ticket_key else ""

        query = """
            INSERT INTO meetings (plan_id, title, scheduled_at, description, meeting_link, status)
            VALUES (%s, %s, %s, %s, %s, 'scheduled')
        """
        meeting_id = execute_write(query, (
            plan_id,
            title,
            meeting_time,
            full_desc,
            jira_link
        ))

        imported_meetings.append({
            "id": meeting_id,
            "title": title,
            "jira_key": ticket_key,
            "scheduled_at": meeting_time
        })

    return jsonify({
        "success": True,
        "message": f"Successfully imported {len(imported_meetings)} Jira ticket(s) into Schedule!",
        "imported_meetings": imported_meetings
    })
