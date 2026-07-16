import os
import logging
import datetime
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from config import Config

logger = logging.getLogger(__name__)

SCOPES = ['https://www.googleapis.com/auth/calendar']

class GoogleCalendarService:
    @staticmethod
    def get_service():
        if not Config.GOOGLE_CLIENT_ID or not Config.GOOGLE_CLIENT_SECRET:
            raise Exception("Google client configuration (GOOGLE_CLIENT_ID or GOOGLE_CLIENT_SECRET) is missing in environment.")
            
        creds = None
        # Look for token.json in workspace BE directory
        token_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'token.json')
        if os.path.exists(token_path):
            creds = Credentials.from_authorized_user_file(token_path, SCOPES)
            
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                raise Exception(f"OAuth credentials file token.json not found or invalid at {token_path}, and cannot auto-refresh.")
                
        return build('calendar', 'v3', credentials=creds)

    @staticmethod
    def create_meeting_event(meeting_id, title, description, start_dt, meeting_link, attendee_emails, timezone="Asia/Kolkata"):
        """
        Creates a Google Calendar event for the meeting and invites the attendees.
        """
        try:
            service = GoogleCalendarService.get_service()
            
            # End time default duration is 1 hour
            end_dt = start_dt + datetime.timedelta(hours=1)
            
            # Format datetime strings
            start_str = start_dt.isoformat()
            end_str = end_dt.isoformat()
            
            # Construct event resource
            event_body = {
                'summary': title,
                'description': description or '',
                'location': meeting_link or '',
                'start': {
                    'dateTime': start_str,
                    'timeZone': timezone,
                },
                'end': {
                    'dateTime': end_str,
                    'timeZone': timezone,
                },
                'attendees': [{'email': email} for email in attendee_emails],
                'reminders': {
                    'useDefault': False,
                    'overrides': [
                        {'method': 'popup', 'minutes': 15},
                        {'method': 'email', 'minutes': 30},
                    ],
                }
            }
            
            # Insert Event
            logger.info(f"Creating Google Calendar event for Meeting ID = {meeting_id}...")
            event = service.events().insert(
                calendarId='primary',
                body=event_body,
                sendUpdates='all'
            ).execute()
            
            event_id = event.get('id')
            html_link = event.get('htmlLink')
            
            logger.info(f"Calendar Event Created: Meeting ID = {meeting_id}")
            logger.info(f"Calendar Event ID: {event_id}")
            logger.info(f"Event Link: {html_link}")
            logger.info(f"Invited Participant Emails: {list(attendee_emails)}")
            
            return {
                "event_id": event_id,
                "event_link": html_link
            }
            
        except Exception as e:
            logger.error(f"Calendar Creation Failed: Meeting ID = {meeting_id}. Error: {e}")
            return None
