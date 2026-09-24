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
            
        token_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'token.json')
        if not os.path.exists(token_path):
            raise Exception("Google Calendar is not connected. Please authenticate using /auth/google/login.")
            
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)
            
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                try:
                    logger.info("Token expired. Refreshing automatically...")
                    creds.refresh(Request())
                    # Write the refreshed credentials back to token.json
                    with open(token_path, 'w') as token_file:
                        token_file.write(creds.to_json())
                    logger.info("Token Refreshed")
                except Exception as refresh_err:
                    raise Exception(f"Google Calendar token refresh failed: {refresh_err}. Please authenticate using /auth/google/login.")
            else:
                raise Exception("Google Calendar is not connected. Please authenticate using /auth/google/login.")
                
        return build('calendar', 'v3', credentials=creds)

    @staticmethod
    def create_meeting_event(meeting_id, title, description, start_dt, meeting_link=None, attendee_emails=[], timezone="Asia/Kolkata", generate_meet_link=False, end_dt=None):
        """
        Creates a Google Calendar event for the meeting and invites the attendees.
        """
        try:
            service = GoogleCalendarService.get_service()
            
            if not end_dt:
                end_dt = start_dt + datetime.timedelta(hours=1)
                
            # Attempt to parse description as JSON agenda
            import json
            try:
                agenda = json.loads(description)
                desc_text = "KT Agenda:\n\n"
                for item in agenda:
                    desc_text += f"[{item.get('time_slot')}] {item.get('topic')} ({item.get('duration')} mins)\n"
                    desc_text += f"Giver(s): {item.get('giver')} | Receiver(s): {item.get('receiver')}\n\n"
                description = desc_text
            except Exception:
                pass # Not JSON, use as-is
            
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
            
            if generate_meet_link:
                import uuid
                event_body['conferenceData'] = {
                    'createRequest': {
                        'requestId': f"meet-{meeting_id}-{uuid.uuid4().hex[:8]}",
                        'conferenceSolutionKey': {
                            'type': 'hangoutsMeet'
                        }
                    }
                }
                logger.info(f"Creating Google Calendar event for Meeting ID = {meeting_id} with Meet link generation...")
                event = service.events().insert(
                    calendarId='primary',
                    body=event_body,
                    conferenceDataVersion=1,
                    sendUpdates='all'
                ).execute()
            else:
                logger.info(f"Creating Google Calendar event for Meeting ID = {meeting_id}...")
                event = service.events().insert(
                    calendarId='primary',
                    body=event_body,
                    sendUpdates='all'
                ).execute()
            
            event_id = event.get('id')
            html_link = event.get('htmlLink')
            hangout_link = event.get('hangoutLink')
            
            logger.info(f"Calendar Event Created: Meeting ID = {meeting_id}")
            logger.info(f"Calendar Event ID: {event_id}")
            logger.info(f"Event Link: {html_link}")
            if hangout_link:
                logger.info(f"Generated Meet Link: {hangout_link}")
            logger.info(f"Invited Participant Emails: {list(attendee_emails)}")
            
            return {
                "event_id": event_id,
                "event_link": html_link,
                "hangout_link": hangout_link
            }
            
        except Exception as e:
            logger.error(f"Calendar Creation Failed: Meeting ID = {meeting_id}. Error: {e}")
            return None
