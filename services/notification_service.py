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

        # 3. Fetch participants (strictly only invited participants mapped in attendance)
        participants_query = """
            SELECT s.name, s.email 
            FROM stakeholders s
            JOIN attendance a ON s.id = a.stakeholder_id
            WHERE a.meeting_id = %s
        """
        participants = execute_query(participants_query, (meeting_id,))
        
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
            <td class="label">Organizer:</td>
            <td class="value">{organizer_name}</td>
          </tr>
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
            
            success = EmailService.send_html_email(email, subject, html_content)
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
