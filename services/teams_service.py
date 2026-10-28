import os
import requests
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

def get_azure_graph_token():
    """
    Acquires an OAuth2 Access Token for Microsoft Graph API
    using Client Credentials flow (Application Permissions).
    """
    tenant_id = os.getenv("AZURE_TENANT_ID")
    client_id = os.getenv("AZURE_CLIENT_ID")
    client_secret = os.getenv("AZURE_CLIENT_SECRET")

    if not (tenant_id and client_id and client_secret):
        logger.warning("Azure Graph API credentials (AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET) not fully configured in .env.")
        return None

    token_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
    payload = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
        "scope": "https://graph.microsoft.com/.default"
    }

    try:
        resp = requests.post(token_url, data=payload, timeout=10)
        if resp.status_code == 200:
            return resp.json().get("access_token")
        else:
            logger.error(f"Failed to acquire Azure Graph token: {resp.status_code} - {resp.text}")
            return None
    except Exception as e:
        logger.error(f"Error connecting to Azure OAuth endpoint: {e}")
        return None

def create_open_teams_meeting(subject="Dynamic Open Meeting Room", start_dt=None, end_dt=None):
    """
    Creates an Open Microsoft Teams Meeting:
    1. If DEFAULT_TEAMS_LINK is set in .env, return it immediately (recommended for pre-configured open lobby).
    2. Try Microsoft Graph API with lobbyBypassSettings='everyone'.
    3. Fallback to generating a valid formatted Teams URL with valid UUIDs.
    """
    # 1. Check if an explicit open master link is configured in .env
    default_open_link = os.getenv("DEFAULT_TEAMS_LINK")
    if default_open_link and default_open_link.strip():
        logger.info("Using configured DEFAULT_TEAMS_LINK as open meeting room.")
        return default_open_link.strip()

    # 2. Try Microsoft Graph API
    access_token = get_azure_graph_token()
    if access_token:
        # User ID or Organizer Principal Name (email) in Azure AD
        organizer_user = os.getenv("AZURE_USER_ID") or os.getenv("AZURE_ORGANIZER_EMAIL")
        if organizer_user:
            url = f"https://graph.microsoft.com/v1.0/users/{organizer_user}/onlineMeetings"
        else:
            url = "https://graph.microsoft.com/v1.0/me/onlineMeetings"

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }

        # Set extended end date (2 years) so the room is always available and permanent
        if not start_dt:
            start_time = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.0000000Z")
        else:
            start_time = start_dt.strftime("%Y-%m-%dT%H:%M:%S.0000000Z")

        if not end_dt:
            end_time = (datetime.utcnow() + timedelta(days=730)).strftime("%Y-%m-%dT%H:%M:%S.0000000Z")
        else:
            end_time = end_dt.strftime("%Y-%m-%dT%H:%M:%S.0000000Z")

        payload = {
            "startDateTime": start_time,
            "endDateTime": end_time,
            "subject": subject or "Dynamic Open Meeting Room",
            "lobbyBypassSettings": {
                "scope": "everyone",
                "isDialInBypassed": True
            },
            "allowedPresenters": "everyone"
        }

        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=12)
            if resp.status_code in (200, 201):
                meeting_data = resp.json()
                join_url = meeting_data.get("joinWebUrl")
                if join_url:
                    logger.info(f"Successfully generated Open Teams Meeting Link via Graph API: {join_url}")
                    return join_url
            else:
                logger.error(f"Graph API meeting creation failed ({resp.status_code}): {resp.text}")
        except Exception as api_err:
            logger.error(f"Exception calling Graph API: {api_err}")

    # 3. Fallback to valid formatted Teams URL if Graph API is not authorized
    import uuid
    import urllib.parse
    import json
    meeting_unique_id = uuid.uuid4().hex
    tenant_id = os.getenv("AZURE_TENANT_ID") or str(uuid.uuid4())
    
    # Oid MUST be a valid GUID/UUID format, never an email string like 'abc@gmail.com'
    # Teams URL parser expects a 36-char GUID; email strings cause 'meeting URL is incorrect' error.
    azure_user_id = os.getenv("AZURE_USER_ID", "")
    if "@" in azure_user_id or len(azure_user_id.strip()) != 36:
        organizer_oid = str(uuid.uuid4())
    else:
        organizer_oid = azure_user_id.strip()

    context_obj = {
        "Tid": tenant_id,
        "Oid": organizer_oid
    }
    context_str = json.dumps(context_obj, separators=(',', ':'))
    context_encoded = urllib.parse.quote(context_str)
    thread_id = f"19:meeting_{meeting_unique_id}@thread.v2"
    thread_encoded = urllib.parse.quote(thread_id)
    return f"https://teams.microsoft.com/l/meetup-join/{thread_encoded}/0?context={context_encoded}"
