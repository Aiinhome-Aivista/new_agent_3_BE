import threading
import logging
from services.email_service import EmailService
from db import execute_query

logger = logging.getLogger(__name__)

def trigger_meeting_notifications(meeting_id):
    """
    Triggers meeting notifications in a background thread.
    """
    thread = threading.Thread(target=_send_meeting_notification_async, args=(meeting_id,))
    thread.daemon = True
    thread.start()
    logger.info(f"Spawned background notification thread for meeting ID: {meeting_id}")

def _send_meeting_notification_async(meeting_id):
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
        subject = f"KT Meeting Scheduled - {meeting['title']}"
        
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
        end_dt = scheduled_dt + timedelta(hours=1)
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
    <div class="header">
      <h1>Knowledge Transfer Meeting</h1>
    </div>
    <div class="content">
      <p>Hello {name},</p>
      <p>A new Knowledge Transfer (KT) meeting has been scheduled. Please find the details below:</p>
      
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
        end_dt = scheduled_dt + timedelta(hours=1)
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
        <strong>Important:</strong> The following KT session has been rescheduled to a new time on the same date. Please update your calendar accordingly.
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

