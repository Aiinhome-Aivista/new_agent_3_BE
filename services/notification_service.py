import threading
import logging
from services.email_service import EmailService
from db import execute_query

logger = logging.getLogger(__name__)

def trigger_meeting_notifications(meeting_id, is_overdue=False):
    """
    Triggers meeting notifications in a background thread.
    """
    thread = threading.Thread(target=_send_meeting_notification_async, args=(meeting_id, is_overdue))
    thread.daemon = True
    thread.start()
    logger.info(f"Spawned background notification thread for meeting ID: {meeting_id} (is_overdue={is_overdue})")

def trigger_overdue_notifications(meeting_id):
    """
    Triggers overdue meeting notifications in a background thread.
    """
    trigger_meeting_notifications(meeting_id, is_overdue=True)

def _send_meeting_notification_async(meeting_id, is_overdue=False):
    logger.info(f"Notification thread started. Meeting Created: Meeting ID = {meeting_id}")
    try:
        from config import Config

        # 1. Fetch meeting info
        meeting_query = """
            SELECT m.title, m.scheduled_at, m.organizer_id, m.plan_id, m.description, m.meeting_link, p.application_name 
            FROM meetings m
            JOIN kt_plans p ON m.plan_id = p.id
            WHERE m.id = %s
        """
        meeting_records = execute_query(meeting_query, (meeting_id,))
        if not meeting_records:
            logger.error(f"Notification Error: Meeting {meeting_id} not found in database.")
            return
        
        meeting = meeting_records[0]
        
        # 2. Fetch organizer info
        organizer_name = "Not specified"
        organizer_email = None
        if meeting.get('organizer_id'):
            org_records = execute_query("SELECT full_name AS name, email FROM users WHERE id = %s", (meeting['organizer_id'],))
            if org_records:
                organizer_name = org_records[0]['name']
                organizer_email = org_records[0]['email']

        # 3. Fetch participants split by role
        participants_query = """
            SELECT s.name, s.email, s.role
            FROM stakeholders s
            JOIN attendance a ON s.id = a.stakeholder_id
            WHERE a.meeting_id = %s
        """
        participants = execute_query(participants_query, (meeting_id,))
        
        knowledge_givers = [p for p in participants if p.get('role') in ('outgoing_sme', 'Outgoing SME (Knowledge Giver)')]
        knowledge_receivers = [p for p in participants if p.get('role') in ('incoming_member', 'Incoming Team Member (Knowledge Receiver)')]
        
        # De-duplicate recipients using dictionary mapping
        recipients = {}
        for p in participants:
            if p.get('email'):
                recipients[p['email']] = p['name']

        # Add organizer if configured to always notify them and they exist
        if Config.ALWAYS_NOTIFY_ORGANIZER and organizer_email:
            recipients[organizer_email] = organizer_name

        # If no participants are selected (and organizer is not notified/exists), do not send any emails
        if not recipients:
            logger.warning("No meeting participants found. Email notification skipped.")
            return

        # 4. Parse date and time
        scheduled_dt = meeting['scheduled_at']
        if isinstance(scheduled_dt, str):
            from datetime import datetime
            try:
                if 'T' in scheduled_dt:
                    scheduled_dt = datetime.strptime(scheduled_dt.replace('T', ' '), "%Y-%m-%d %H:%M:%S")
                else:
                    scheduled_dt = datetime.strptime(scheduled_dt, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                try:
                    scheduled_dt = datetime.fromisoformat(scheduled_dt)
                except ValueError:
                    pass

        if hasattr(scheduled_dt, 'strftime'):
            meeting_date = scheduled_dt.strftime("%A, %B %d, %Y")
            meeting_time = scheduled_dt.strftime("%I:%M %p")
        else:
            meeting_date = str(scheduled_dt)
            meeting_time = ""

        timezone = "IST"  # Default local system/workspace timezone

        # 5. Build and send HTML emails to each participant
        if is_overdue:
            subject = f"OVERDUE: KT Meeting - {meeting['title']}"
            header_title = "KT Session Overdue Notice"
            header_color = "#dc2626"
            intro_text = f"""<p><strong style="color: #dc2626;">OVERDUE NOTICE:</strong> This Knowledge Transfer (KT) meeting was scheduled for <strong>{meeting_date}</strong> and is currently marked as <strong>Overdue</strong> (not completed).</p>"""
            action_box = """
      <div style="background-color: #fef2f2; border-left: 4px solid #dc2626; padding: 12px; margin-top: 16px; border-radius: 4px; color: #991b1b;">
        <strong>Action Required:</strong> Please coordinate with your team to either reschedule this meeting to a future date or record attendance and mark it completed.
      </div>
            """
        else:
            subject = f"KT Meeting Scheduled - {meeting['title']}"
            header_title = "Knowledge Transfer Meeting"
            header_color = "#0052cc"
            intro_text = "<p>A new Knowledge Transfer (KT) meeting has been scheduled. Please find the details below:</p>"
            action_box = ""
        
        # Format description and meeting link optional fields
        description_row = ""
        if meeting.get('description'):
            description_row = f"""
          <tr>
            <td class="label">Description:</td>
            <td class="value">{meeting['description']}</td>
          </tr>
            """

        link_row = ""
        if meeting.get('meeting_link'):
            link_row = f"""
          <tr>
            <td class="label">Meeting Link:</td>
            <td class="value"><a href="{meeting['meeting_link']}" style="color: #3b82f6; text-decoration: underline;">{meeting['meeting_link']}</a></td>
          </tr>
            """

        # Generate ICS Content
        import uuid
        from datetime import datetime, timedelta
        
        # Calculate start and end times in the correct format for ICS
        start_ics = scheduled_dt.strftime("%Y%m%dT%H%M%S")
        end_dt = scheduled_dt + timedelta(hours=2)
        end_ics = end_dt.strftime("%Y%m%dT%H%M%S")
        now_ics = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        
        # Build attendee strings for ICS
        attendees_ics = ""
        for p in participants:
            if p.get('email'):
                attendees_ics += f"ATTENDEE;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;RSVP=TRUE;CN={p.get('name')}:mailto:{p['email']}\n"
        
        ics_content = f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//KT Manager//EN
METHOD:REQUEST
BEGIN:VEVENT
UID:{uuid.uuid4()}@ktmanager.local
DTSTAMP:{now_ics}
DTSTART;TZID=Asia/Kolkata:{start_ics}
DTEND;TZID=Asia/Kolkata:{end_ics}
SUMMARY:{meeting['title']}
DESCRIPTION:{meeting.get('description', 'KT Meeting')}
LOCATION:{meeting.get('meeting_link', '')}
ORGANIZER;CN={organizer_name}:mailto:{organizer_email or 'no-reply@ktmanager.local'}
{attendees_ics.strip()}
END:VEVENT
END:VCALENDAR"""

        givers_str = ", ".join([p.get('name', 'Unknown') for p in knowledge_givers]) or "Not specified"
        receivers_str = ", ".join([p.get('name', 'Unknown') for p in knowledge_receivers]) or "Not specified"
        givers_row = f"""
          <tr>
            <td class="label">Knowledge Givers:</td>
            <td class="value">{givers_str}</td>
          </tr>
        """
        receivers_row = f"""
          <tr>
            <td class="label">Participants:</td>
            <td class="value">{receivers_str}</td>
          </tr>
        """

        for email, name in recipients.items():

            html_content = f"""<!DOCTYPE html>
<html>
<head>
  <style>
    body {{
      font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
      line-height: 1.6;
      color: #333333;
      background-color: #f4f6f8;
      margin: 0;
      padding: 0;
    }}
    .container {{
      max-width: 600px;
      margin: 20px auto;
      background-color: #ffffff;
      border-radius: 8px;
      box-shadow: 0 4px 6px rgba(0, 0, 0, 0.05);
      border: 1px solid #e1e4e8;
      overflow: hidden;
    }}
    .header {{
      background: linear-gradient(135deg, #1e3a8a, #3b82f6);
      color: #ffffff;
      padding: 30px 20px;
      text-align: center;
    }}
    .header h1 {{
      margin: 0;
      font-size: 24px;
      font-weight: 600;
    }}
    .content {{
      padding: 30px 20px;
    }}
    .meeting-details {{
      background-color: #f8fafc;
      border-left: 4px solid #3b82f6;
      padding: 20px;
      margin: 20px 0;
      border-radius: 0 8px 8px 0;
    }}
    .meeting-details table {{
      width: 100%;
      border-collapse: collapse;
    }}
    .meeting-details td {{
      padding: 8px 0;
      vertical-align: top;
    }}
    .label {{
      font-weight: bold;
      color: #475569;
      width: 130px;
    }}
    .value {{
      color: #1e293b;
    }}
    .footer {{
      background-color: #f1f5f9;
      color: #64748b;
      padding: 15px 20px;
      text-align: center;
      font-size: 12px;
      border-top: 1px solid #e2e8f0;
    }}
  </style>
</head>
<body>
  <div class="container">
    <div class="header" style="background-color: {header_color};">
      <h1>{header_title}</h1>
    </div>
    <div class="content">
      <p>Hello {name},</p>
      {intro_text}
      
      <div class="meeting-details">
        <table>
          <tr>
            <td class="label">Meeting Title:</td>
            <td class="value">{meeting['title']}</td>
          </tr>
          <tr>
            <td class="label">Project/Plan:</td>
            <td class="value">{meeting['application_name']}</td>
          </tr>
          <tr>
            <td class="label">Organizer: </td>
            <td class="value">{organizer_name}</td>
          </tr>
          {givers_row}
          {receivers_row}
          <tr>
            <td class="label">Date:</td>
            <td class="value">{meeting_date}</td>
          </tr>
          <tr>
            <td class="label">Time:</td>
            <td class="value">{meeting_time} ({timezone})</td>
          </tr>
          {description_row}
          {link_row}
        </table>
      </div>
      {action_box}
      <p>Please make sure to update your calendar and join on time.</p>
      <p>Best regards,<br><strong>KT Manager Notification Service</strong></p>
    </div>
    <div class="footer">
      This is an automated notification from the PwC KT Manager application. Please do not reply directly to this email.
    </div>
  </div>
</body>
</html>"""
            
            success = EmailService.send_html_email(email, subject, html_content, ics_content=ics_content)
            if success:
                logger.info(f"Email Sent: Meeting ID = {meeting_id}, Recipient Email = {email}")
            else:
                logger.error(f"Email Failed: Meeting ID = {meeting_id}, Recipient Email = {email}")
                
        # 6. Trigger Google Calendar event creation
        try:
            from services.google_calendar_service import GoogleCalendarService
            attendee_emails = list(recipients.keys())
            if attendee_emails:
                GoogleCalendarService.create_meeting_event(
                    meeting_id=meeting_id,
                    title=meeting['title'],
                    description=meeting.get('description'),
                    start_dt=scheduled_dt,
                    meeting_link=meeting.get('meeting_link'),
                    attendee_emails=attendee_emails
                )
        except Exception as cal_err:
            logger.error(f"Calendar Creation Failed: Meeting ID = {meeting_id}. Error: {cal_err}")
            
    except Exception as e:
        logger.error(f"Notification Service error for meeting {meeting_id}: {e}")


# ─────────────────────────────────────────────
#  Reschedule Notification
# ─────────────────────────────────────────────

def trigger_reschedule_notifications(meeting_id, new_scheduled_dt, reason=""):
    """
    Triggers reschedule notifications in a background thread.
    new_scheduled_dt: datetime object with the updated date+time.
    reason: optional string explaining why the meeting was rescheduled.
    """
    thread = threading.Thread(
        target=_send_reschedule_notification_async,
        args=(meeting_id, new_scheduled_dt, reason)
    )
    thread.daemon = True
    thread.start()
    logger.info(f"Spawned background reschedule-notification thread for meeting ID: {meeting_id}")


def _send_reschedule_notification_async(meeting_id, new_scheduled_dt, reason=""):
    logger.info(f"Reschedule notification thread started for Meeting ID = {meeting_id}")
    try:
        from config import Config

        # 1. Fetch updated meeting info
        meeting_query = """
            SELECT m.title, m.scheduled_at, m.organizer_id, m.plan_id, m.description, m.meeting_link, p.application_name 
            FROM meetings m
            JOIN kt_plans p ON m.plan_id = p.id
            WHERE m.id = %s
        """
        meeting_records = execute_query(meeting_query, (meeting_id,))
        if not meeting_records:
            logger.error(f"Reschedule Notification Error: Meeting {meeting_id} not found in database.")
            return

        meeting = meeting_records[0]

        # 2. Fetch organizer info
        organizer_name = "Not specified"
        organizer_email = None
        if meeting.get('organizer_id'):
            org_records = execute_query("SELECT full_name AS name, email FROM users WHERE id = %s", (meeting['organizer_id'],))
            if org_records:
                organizer_name = org_records[0]['name']
                organizer_email = org_records[0]['email']

        # 3. Fetch participants
        participants_query = """
            SELECT s.name, s.email, s.role
            FROM stakeholders s
            JOIN attendance a ON s.id = a.stakeholder_id
            WHERE a.meeting_id = %s
        """
        participants = execute_query(participants_query, (meeting_id,))

        knowledge_givers = [p for p in participants if p.get('role') in ('outgoing_sme', 'Outgoing SME (Knowledge Giver)')]
        knowledge_receivers = [p for p in participants if p.get('role') in ('incoming_member', 'Incoming Team Member (Knowledge Receiver)')]

        recipients = {}
        for p in participants:
            if p.get('email'):
                recipients[p['email']] = p['name']

        if Config.ALWAYS_NOTIFY_ORGANIZER and organizer_email:
            recipients[organizer_email] = organizer_name

        if not recipients:
            logger.warning(f"No participants found for reschedule notification on meeting {meeting_id}. Skipped.")
            return

        # 4. Format new date/time
        from datetime import datetime, timedelta
        scheduled_dt = new_scheduled_dt
        if isinstance(scheduled_dt, str):
            try:
                scheduled_dt = datetime.fromisoformat(scheduled_dt)
            except ValueError:
                scheduled_dt = datetime.strptime(scheduled_dt, "%Y-%m-%d %H:%M:%S")

        if hasattr(scheduled_dt, 'strftime'):
            meeting_date = scheduled_dt.strftime("%A, %B %d, %Y")
            meeting_time = scheduled_dt.strftime("%I:%M %p")
        else:
            meeting_date = str(scheduled_dt)
            meeting_time = ""

        timezone = "IST"

        # 5. Build ICS for updated invite
        import uuid
        start_ics = scheduled_dt.strftime("%Y%m%dT%H%M%S")
        end_dt = scheduled_dt + timedelta(hours=2)
        end_ics = end_dt.strftime("%Y%m%dT%H%M%S")
        now_ics = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")

        attendees_ics = ""
        for p in participants:
            if p.get('email'):
                attendees_ics += f"ATTENDEE;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;RSVP=TRUE;CN={p.get('name')}:mailto:{p['email']}\n"

        ics_content = f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//KT Manager//EN
METHOD:REQUEST
BEGIN:VEVENT
UID:{uuid.uuid4()}@ktmanager.local
DTSTAMP:{now_ics}
DTSTART;TZID=Asia/Kolkata:{start_ics}
DTEND;TZID=Asia/Kolkata:{end_ics}
SUMMARY:[RESCHEDULED] {meeting['title']}
DESCRIPTION:{meeting.get('description', 'KT Meeting')} — Rescheduled.
LOCATION:{meeting.get('meeting_link', '')}
ORGANIZER;CN={organizer_name}:mailto:{organizer_email or 'no-reply@ktmanager.local'}
{attendees_ics.strip()}
END:VEVENT
END:VCALENDAR"""

        # 6. Build optional rows
        givers_str = ", ".join([p.get('name', 'Unknown') for p in knowledge_givers]) or "Not specified"
        receivers_str = ", ".join([p.get('name', 'Unknown') for p in knowledge_receivers]) or "Not specified"

        link_row = ""
        if meeting.get('meeting_link'):
            link_row = f"""
          <tr>
            <td class="label">Meeting Link:</td>
            <td class="value"><a href="{meeting['meeting_link']}" style="color: #d97706; text-decoration: underline;">{meeting['meeting_link']}</a></td>
          </tr>
            """

        reason_row = ""
        if reason and reason.strip():
            reason_row = f"""
          <tr>
            <td class="label">Reason:</td>
            <td class="value" style="color:#92400e;">{reason.strip()}</td>
          </tr>
            """

        subject = f"[RESCHEDULED] KT Meeting - {meeting['title']}"

        # 7. Send HTML email to each recipient
        for email, name in recipients.items():
            html_content = f"""<!DOCTYPE html>
<html>
<head>
  <style>
    body {{
      font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
      line-height: 1.6;
      color: #333333;
      background-color: #fef9f0;
      margin: 0;
      padding: 0;
    }}
    .container {{
      max-width: 600px;
      margin: 20px auto;
      background-color: #ffffff;
      border-radius: 8px;
      box-shadow: 0 4px 6px rgba(0, 0, 0, 0.08);
      border: 1px solid #fcd34d;
      overflow: hidden;
    }}
    .header {{
      background: linear-gradient(135deg, #b45309, #d97706);
      color: #ffffff;
      padding: 30px 20px;
      text-align: center;
    }}
    .header h1 {{
      margin: 0 0 4px 0;
      font-size: 22px;
      font-weight: 700;
    }}
    .header p {{
      margin: 0;
      font-size: 14px;
      opacity: 0.9;
    }}
    .badge {{
      display: inline-block;
      background-color: #fef3c7;
      color: #92400e;
      font-weight: 700;
      font-size: 12px;
      padding: 3px 10px;
      border-radius: 12px;
      margin-top: 10px;
      letter-spacing: 0.5px;
    }}
    .content {{
      padding: 30px 20px;
    }}
    .alert-box {{
      background-color: #fffbeb;
      border: 1px solid #fcd34d;
      border-left: 4px solid #d97706;
      border-radius: 6px;
      padding: 14px 18px;
      margin-bottom: 20px;
      font-size: 14px;
      color: #78350f;
    }}
    .meeting-details {{
      background-color: #f8fafc;
      border-left: 4px solid #d97706;
      padding: 20px;
      margin: 20px 0;
      border-radius: 0 8px 8px 0;
    }}
    .meeting-details table {{
      width: 100%;
      border-collapse: collapse;
    }}
    .meeting-details td {{
      padding: 8px 0;
      vertical-align: top;
    }}
    .label {{
      font-weight: bold;
      color: #475569;
      width: 140px;
    }}
    .value {{
      color: #1e293b;
    }}
    .new-time {{
      color: #b45309;
      font-weight: 700;
      font-size: 15px;
    }}
    .footer {{
      background-color: #fef3c7;
      color: #92400e;
      padding: 15px 20px;
      text-align: center;
      font-size: 12px;
      border-top: 1px solid #fcd34d;
    }}
  </style>
</head>
<body>
  <div class="container">
    <div class="header">
      <h1>&#x1F550; Meeting Rescheduled</h1>
      <p>Knowledge Transfer Session — Time Update</p>
      <span class="badge">&#x26A0;&#xFE0F; ACTION REQUIRED: Update Your Calendar</span>
    </div>
    <div class="content">
      <p>Hello {name},</p>
      <div class="alert-box">
        <strong>Important:</strong> The following KT session has been rescheduled. Please update your calendar accordingly.
      </div>

      <div class="meeting-details">
        <table>
          <tr>
            <td class="label">Meeting Title:</td>
            <td class="value">{meeting['title']}</td>
          </tr>
          <tr>
            <td class="label">Project/Plan:</td>
            <td class="value">{meeting['application_name']}</td>
          </tr>
          <tr>
            <td class="label">Organizer:</td>
            <td class="value">{organizer_name}</td>
          </tr>
          <tr>
            <td class="label">Knowledge Givers:</td>
            <td class="value">{givers_str}</td>
          </tr>
          <tr>
            <td class="label">Participants:</td>
            <td class="value">{receivers_str}</td>
          </tr>
          <tr>
            <td class="label">Date:</td>
            <td class="value">{meeting_date}</td>
          </tr>
          <tr>
            <td class="label">New Time:</td>
            <td class="value new-time">&#x1F551; {meeting_time} ({timezone})</td>
          </tr>
          {reason_row}
          {link_row}
        </table>
      </div>

      <p>An updated calendar invite is attached. Please accept it to replace the previous time in your calendar.</p>
      <p>Best regards,<br><strong>KT Manager Notification Service</strong></p>
    </div>
    <div class="footer">
      This is an automated reschedule notification from the PwC KT Manager application. Please do not reply to this email.
    </div>
  </div>
</body>
</html>"""

            success = EmailService.send_html_email(email, subject, html_content, ics_content=ics_content)
            if success:
                logger.info(f"Reschedule Email Sent: Meeting ID = {meeting_id}, Recipient = {email}")
            else:
                logger.error(f"Reschedule Email Failed: Meeting ID = {meeting_id}, Recipient = {email}")

    except Exception as e:
        logger.error(f"Reschedule Notification Service error for meeting {meeting_id}: {e}")

def trigger_final_assessment_reminder(plan_id):
    """
    Triggers an email notification to Knowledge Receiver(s) if KT topics for the plan
    are completed (or unlocked by manager) and they haven't taken the Final Assessment yet.
    If the deadline window has expired, sends an Expired Notice instead.
    Runs asynchronously in a background thread.
    """
    thread = threading.Thread(target=_send_final_assessment_reminder_async, args=(plan_id,))
    thread.daemon = True
    thread.start()
    logger.info(f"Spawned background Final Assessment reminder thread for plan ID: {plan_id}")

def _send_final_assessment_reminder_async(plan_id):
    logger.info(f"Final Assessment reminder thread started for plan ID: {plan_id}")
    try:
        from config import Config
        from datetime import datetime

        # 1. Fetch plan info
        plan_query = "SELECT id, application_name, is_final_unlocked, final_deadline_extension_days FROM kt_plans WHERE id = %s"
        plan_res = execute_query(plan_query, (plan_id,))
        if not plan_res:
            logger.error(f"Final Assessment Reminder Error: Plan {plan_id} not found.")
            return

        plan_info = plan_res[0]
        app_name = plan_info.get('application_name') or f"Plan #{plan_id}"
        is_unlocked = bool(plan_info.get('is_final_unlocked'))
        deadline_days = plan_info.get('final_deadline_extension_days') or 90

        # 2. Check if plan topics are completed or if manager unlocked
        comp_query = """
            SELECT 
                (SELECT COUNT(*) FROM completion_tracking WHERE plan_id = %s AND completion_percent = 100) as completed_topics,
                (SELECT COUNT(*) FROM plan_topics WHERE plan_id = %s) as total_topics
            FROM DUAL
        """
        comp_res = execute_query(comp_query, (plan_id, plan_id))
        completed_cnt = comp_res[0]['completed_topics'] if comp_res else 0
        total_cnt = comp_res[0]['total_topics'] if comp_res else 0

        is_plan_completed = total_cnt > 0 and completed_cnt >= total_cnt

        if not is_plan_completed and not is_unlocked and completed_cnt == 0:
            logger.info(f"Final Assessment Reminder Skipped: Plan {plan_id} has no completed topics and not unlocked by manager.")
            return

        # Check completion timestamps to determine if deadline expired
        comp_time_query = "SELECT MAX(last_updated) as max_time FROM completion_tracking WHERE plan_id = %s AND completion_percent = 100"
        time_res = execute_query(comp_time_query, (plan_id,))
        max_time = time_res[0]['max_time'] if time_res else None
        
        is_expired = False
        elapsed_days = 0
        if max_time:
            if isinstance(max_time, str):
                try:
                    max_time = datetime.fromisoformat(max_time.replace('Z', ''))
                except Exception:
                    max_time = None
            if max_time:
                elapsed_days = max(0, (datetime.now() - max_time).days)
                if elapsed_days >= deadline_days:
                    is_expired = True

        # 3. Find Knowledge Receivers associated with this plan or in system
        receivers_query = """
            SELECT DISTINCT s.id, s.name, s.email 
            FROM stakeholders s
            WHERE (s.role IN ('Incoming Team Member (Knowledge Receiver)', 'incoming_member') OR s.role LIKE '%incoming%' OR s.role LIKE '%Receiver%')
              AND s.email IS NOT NULL AND s.email != ''
        """
        receivers = execute_query(receivers_query)
        if not receivers:
            logger.warning(f"Final Assessment Reminder Skipped: No Knowledge Receivers found for plan {plan_id}.")
            return

        # 4. Filter receivers who have NOT completed the Final Assessment yet
        pending_receivers = []
        for r in receivers:
            s_id = r['id']
            res_query = "SELECT id FROM assessment_results WHERE plan_id = %s AND stakeholder_id = %s AND assessment_type = 'final'"
            res_rows = execute_query(res_query, (plan_id, s_id))
            if not res_rows:
                pending_receivers.append(r)

        if not pending_receivers:
            logger.info(f"Final Assessment Reminder Skipped: All Knowledge Receivers have already completed the Final Assessment for plan {plan_id}.")
            return

        # 5. Send Email Reminder OR Expired Notice to each pending Knowledge Receiver
        for r in pending_receivers:
            recipient_email = r['email']
            recipient_name = r.get('name') or "Knowledge Receiver"
            
            if is_expired:
                subject = f"Notice: Final Assessment Window Expired for {app_name}"
                html_content = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <style>
    body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f4f5f7; margin: 0; padding: 20px; }}
    .card {{ background-color: #ffffff; max-width: 600px; margin: 0 auto; border-radius: 12px; padding: 30px; box-shadow: 0 4px 12px rgba(0,0,0,0.05); border: 1px solid #e5e7eb; }}
    .header {{ border-bottom: 2px solid #ef4444; padding-bottom: 15px; margin-bottom: 20px; }}
    .title {{ font-size: 20px; color: #991b1b; font-weight: bold; margin: 0; }}
    .badge {{ display: inline-block; background-color: #fee2e2; color: #991b1b; padding: 4px 12px; border-radius: 20px; font-size: 12px; font-weight: bold; margin-top: 8px; }}
    .body-text {{ font-size: 14px; color: #374151; line-height: 1.6; margin-top: 15px; }}
    .expired-box {{ background-color: #fef2f2; border: 1px solid #fca5a5; border-radius: 8px; padding: 15px; margin: 20px 0; font-size: 13px; color: #991b1b; line-height: 1.6; }}
    .footer {{ font-size: 12px; color: #6b7280; text-align: center; margin-top: 30px; border-top: 1px solid #f3f4f6; padding-top: 15px; }}
  </style>
</head>
<body>
  <div class="card">
    <div class="header">
      <h2 class="title">Final Assessment Window Expired</h2>
      <span class="badge">PwC Knowledge Transfer Portal</span>
    </div>
    
    <p class="body-text">Dear <strong>{recipient_name}</strong>,</p>
    
    <p class="body-text">
      This is to inform you that the deadline window for taking your <strong>Final Assessment</strong> for <strong>{app_name}</strong> has expired.
    </p>
    
    <div class="expired-box">
      🚫 <strong>Status:</strong> Assessment Window Closed<br/>
      ⌛ <strong>Time Elapsed:</strong> {elapsed_days} day(s) have passed (Deadline Limit: {deadline_days} days).<br/>
      ⚠️ <strong>Action Required:</strong> You can no longer start this assessment unless your Delivery Manager extends the deadline window.
    </div>
    
    <p class="body-text">
      If you still need to complete your Final Assessment, please reach out to your <strong>Delivery Manager</strong> to request a deadline extension.
    </p>
    
    <div class="footer">
      This is an automated notification from the PwC KT Manager application. Please do not reply directly to this email.
    </div>
  </div>
</body>
</html>"""
            else:
                subject = f"Action Required: Final Assessment Pending for {app_name}"
                html_content = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <style>
    body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f4f5f7; margin: 0; padding: 20px; }}
    .card {{ background-color: #ffffff; max-width: 600px; margin: 0 auto; border-radius: 12px; padding: 30px; box-shadow: 0 4px 12px rgba(0,0,0,0.05); border: 1px solid #e5e7eb; }}
    .header {{ border-bottom: 2px solid #6366f1; padding-bottom: 15px; margin-bottom: 20px; }}
    .title {{ font-size: 20px; color: #1e1b4b; font-weight: bold; margin: 0; }}
    .badge {{ display: inline-block; background-color: #e0e7ff; color: #3730a3; padding: 4px 12px; border-radius: 20px; font-size: 12px; font-weight: bold; margin-top: 8px; }}
    .body-text {{ font-size: 14px; color: #374151; line-height: 1.6; margin-top: 15px; }}
    .highlight-box {{ background-color: #f5f3ff; border: 1px solid #ddd6fe; border-radius: 8px; padding: 15px; margin: 20px 0; font-size: 13px; color: #4c1d95; }}
    .footer {{ font-size: 12px; color: #6b7280; text-align: center; margin-top: 30px; border-top: 1px solid #f3f4f6; padding-top: 15px; }}
  </style>
</head>
<body>
  <div class="card">
    <div class="header">
      <h2 class="title">Final Assessment Pending</h2>
      <span class="badge">PwC Knowledge Transfer Portal</span>
    </div>
    
    <p class="body-text">Dear <strong>{recipient_name}</strong>,</p>
    
    <p class="body-text">
      The Knowledge Transfer topics for <strong>{app_name}</strong> are ready for final evaluation. 
      This is a reminder that your <strong>Final Assessment</strong> has not been taken yet.
    </p>
    
    <div class="highlight-box">
      ⏰ <strong>Assessment Deadline Window:</strong> You have <strong>{max(0, deadline_days - elapsed_days)} days</strong> remaining to complete your Final Assessment.<br/>
      🎯 <strong>Mode:</strong> Final Assessment (Mandatory)
    </div>
    
    <p class="body-text">
      Please log in to the PwC KT Portal and complete your Final Assessment under the <strong>Assessment</strong> section.
    </p>
    
    <div class="footer">
      This is an automated notification from the PwC KT Manager application. Please do not reply directly to this email.
    </div>
  </div>
</body>
</html>"""
            
            success = EmailService.send_html_email(recipient_email, subject, html_content)
            if success:
                logger.info(f"Final Assessment Notification Email Sent to {recipient_email} for plan {plan_id} (is_expired={is_expired})")
            else:
                logger.error(f"Final Assessment Notification Email Failed to {recipient_email} for plan {plan_id}")

    except Exception as e:
        logger.error(f"Error in Final Assessment Reminder notification thread: {e}")

