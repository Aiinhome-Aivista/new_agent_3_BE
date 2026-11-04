import os
import re
import time
import urllib.parse
import threading
import logging
import tempfile
import shutil
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Set, Tuple
from playwright.sync_api import sync_playwright

from config import Config
from db import execute_query, execute_write

logger = logging.getLogger("ai_meeting.browser_bot")

class BrowserMeetingBot:
    _active_bots: Dict[int, threading.Thread] = {}
    _stop_signals: Dict[int, threading.Event] = {}

    @classmethod
    def is_active(cls, meeting_id: int) -> bool:
        """Returns True if a bot worker thread is currently running for this meeting."""
        thread = cls._active_bots.get(meeting_id)
        return thread is not None and thread.is_alive()

    @classmethod
    def start_bot(cls, meeting_id: int, join_url: str, headless: Optional[bool] = None, force_restart: bool = False):
        """Starts browser bot in a background daemon thread to join the real live meeting."""
        if cls.is_active(meeting_id) and not force_restart:
            logger.info(f"Browser meeting bot already active for meeting #{meeting_id}. Ignoring duplicate start.")
            return

        if force_restart:
            cls.stop_bot(meeting_id)

        if headless is None:
            headless = Config.BOT_HEADLESS

        stop_event = threading.Event()
        cls._stop_signals[meeting_id] = stop_event

        thread = threading.Thread(
            target=cls._run_bot_lifecycle,
            args=(meeting_id, join_url, stop_event, headless),
            daemon=True
        )
        cls._active_bots[meeting_id] = thread
        thread.start()
        logger.info(f"Browser meeting bot worker started for meeting #{meeting_id} (headless={headless})")

    @classmethod
    def stop_bot(cls, meeting_id: int):
        """Signals active bot to disconnect and clean up."""
        if meeting_id in cls._stop_signals:
            cls._stop_signals[meeting_id].set()
            cls._stop_signals.pop(meeting_id, None)
        cls._active_bots.pop(meeting_id, None)

    @classmethod
    def _run_bot_lifecycle(cls, meeting_id: int, join_url: str, stop_event: threading.Event, headless: bool):
        from services.bot.analysis_service import AnalysisEngine

        captured_roster: List[Dict[str, Any]] = []
        captured_speech: List[Dict[str, Any]] = []
        completed_normally: bool = False
        start_ts = time.time()

        try:
            # Preload Knowledge Giver and registered attendees for speaker resolution
            meeting_giver = None
            known_attendees = []
            try:
                # 1. Fetch registered attendees from attendance table & identify Outgoing SME (Knowledge Giver)
                att_info = execute_query(
                    """SELECT s.id, s.name, s.role FROM attendance a 
                       JOIN stakeholders s ON a.stakeholder_id = s.id 
                       WHERE a.meeting_id = %s""",
                    (meeting_id,)
                )
                for ar in (att_info or []):
                    s_name = ar.get("name")
                    s_role = (ar.get("role") or "").lower()
                    if s_name:
                        known_attendees.append(s_name)
                        if "giver" in s_role or "outgoing" in s_role or s_role == "outgoing_sme":
                            meeting_giver = s_name

                # 2. If meeting_giver not found in attendance, check global Outgoing SME
                if not meeting_giver:
                    global_kg = execute_query(
                        """SELECT name FROM stakeholders 
                           WHERE role LIKE '%Giver%' OR role LIKE '%outgoing%' OR role = 'outgoing_sme' 
                           LIMIT 1"""
                    )
                    if global_kg and global_kg[0].get("name"):
                        meeting_giver = global_kg[0]["name"]

                logger.info(f"[Preload] Meeting #{meeting_id} Knowledge Giver: '{meeting_giver}', Known Attendees: {known_attendees}")
            except Exception as preload_err:
                logger.debug(f"Preload attendees note: {preload_err}")

            with sync_playwright() as p:
                profile_dir = tempfile.mkdtemp(prefix=f"teams_bot_{meeting_id}_")

                logger.info(f"Launching Chromium context for meeting #{meeting_id} (Meet Agent pure Live Captions mode)...")
                context = p.chromium.launch_persistent_context(
                    user_data_dir=profile_dir,
                    headless=headless,
                    permissions=["microphone", "camera"],
                    args=[
                        "--use-fake-ui-for-media-stream",
                        "--use-fake-device-for-media-stream",
                        "--disable-blink-features=AutomationControlled",
                        "--no-sandbox",
                        "--disable-setuid-sandbox",
                        "--disable-notifications",
                        "--autoplay-policy=no-user-gesture-required",
                    ],
                    viewport={"width": 1280, "height": 800},
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                )
                page = context.pages[0] if context.pages else context.new_page()

                # Bypass launcher page for short Teams meet links
                navigate_url = join_url
                if "teams.microsoft.com" in join_url or "teams.live.com" in join_url:
                    parsed = urllib.parse.urlparse(join_url)
                    qs = urllib.parse.parse_qs(parsed.query)
                    passcode = qs.get("p", [None])[0]

                    if "/meet/" in parsed.path and passcode:
                        meet_code = parsed.path.split("/meet/")[-1].strip("/")
                        direct = f"https://teams.microsoft.com/v2/?meetingjoin=true#/meet/{meet_code}?p={passcode}&anon=true"
                        logger.info(f"[Teams Bot] Web join link constructed → {direct[:100]}")
                        navigate_url = direct
                    else:
                        logger.info(f"[Teams Bot] Navigating to Teams URL: {join_url[:100]}")

                logger.info(f"[Bot] Navigating to meeting URL: {navigate_url[:100]}...")
                try:
                    page.goto(navigate_url, wait_until="domcontentloaded", timeout=90000)
                except Exception as nav_err:
                    logger.warning(f"[Bot] goto warning (may still be loading): {nav_err}")

                # Run session based on platform
                if "teams.microsoft.com" in join_url or "teams.live.com" in join_url:
                    captured_roster, captured_speech, completed_normally = cls._run_teams_session(
                        page, meeting_id, stop_event, captured_roster, captured_speech,
                        known_attendees=known_attendees
                    )
                elif "meet.google.com" in join_url:
                    captured_roster, captured_speech, completed_normally = cls._run_google_meet_session(
                        page, meeting_id, stop_event, captured_roster, captured_speech
                    )
                else:
                    logger.warning(f"Unrecognized meeting URL: {join_url}, trying generic teams session...")
                    captured_roster, captured_speech, completed_normally = cls._run_teams_session(
                        page, meeting_id, stop_event, captured_roster, captured_speech,
                        known_attendees=known_attendees
                    )

                try:
                    context.close()
                except Exception:
                    pass
                if profile_dir and os.path.exists(profile_dir):
                    shutil.rmtree(profile_dir, ignore_errors=True)

        except Exception as e:
            logger.error(f"Browser bot error for meeting #{meeting_id}: {e}", exc_info=True)
            if completed_normally or len(captured_roster) > 0 or len(captured_speech) > 0:
                logger.info(f"Meeting #{meeting_id} had active call presence; preserving completed_normally=True despite browser exit.")
                completed_normally = True
            else:
                completed_normally = False
            try:
                execute_write(
                    "UPDATE scheduled_bot_jobs SET status = 'FAILED', error_message = %s WHERE meeting_id = %s",
                    (str(e)[:500], meeting_id)
                )
            except Exception:
                pass
        finally:
            cls.stop_bot(meeting_id)

            if completed_normally:
                logger.info(f"Triggering Post-Meeting Intelligence Pipeline for meeting #{meeting_id}...")
                try:
                    AnalysisEngine.run_post_meeting_pipeline(
                        meeting_id=meeting_id,
                        roster_participants=captured_roster,
                        speech_segments=captured_speech
                    )
                    execute_write(
                        "UPDATE scheduled_bot_jobs SET status = 'COMPLETED', error_message = NULL WHERE meeting_id = %s",
                        (meeting_id,)
                    )
                except Exception as pipe_err:
                    logger.error(f"Post-meeting pipeline error: {pipe_err}", exc_info=True)
            else:
                logger.warning(
                    f"Meeting #{meeting_id} did not complete normally (completed_normally={completed_normally}). "
                    "Skipping post-meeting analysis pipeline; meeting status remains unaffected."
                )

    @classmethod
    def _run_teams_session(
        cls,
        page,
        meeting_id: int,
        stop_event: threading.Event,
        roster_participants: Optional[List[Dict[str, Any]]] = None,
        speech_segments: Optional[List[Dict[str, Any]]] = None,
        known_attendees: Optional[List[str]] = None
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], bool]:
        """Automates MS Teams join, lobby waiting, participant scraping, and live captions."""
        logger.info(f"[Teams Bot] Starting session for meeting #{meeting_id} (pure Teams Live Captions)...")
        bot_name = Config.BOT_NAME
        if roster_participants is None:
            roster_participants = []
        if speech_segments is None:
            speech_segments = []
        if known_attendees is None:
            known_attendees = []
        seen_captions: Set[str] = set()
        seen_names: Set[str] = set()
        for p in roster_participants:
            if p.get("name"):
                seen_names.add(p["name"])

        in_call = False
        in_lobby = False
        has_ever_joined_call = False
        completed_normally = False
        start_ts = time.time()
        in_call_start_ts = 0.0
        join_clicked = False
        roster_panel_opened = False
        captions_enabled = False
        last_caption_toggle_ts = 0.0
        last_nudge_ts = 0.0
        controls_missing_count = 0
        alone_consecutive_count = 0

        SYSTEM_UI_WORDS = {
            "attendees", "organizer", "organizers", "presenters", "others invited",
            "in this meeting", "waiting in lobby", "lobby", bot_name.lower(),
            "mute all", "share invite", "more options", "search", "people",
            "activity", "chat", "teams", "calendar", "calls", "apps", "files",
            "help", "settings", "feedback", "unmute", "mute", "camera", "share",
            "reactions", "more", "leave", "hang up", "devices", "view", "raise", "react"
        }

        # Step 1: Pre-join loop (wait up to 60s for launcher / pre-join and click Join)
        for step in range(60):
            if stop_event.is_set():
                logger.info(f"[Teams Bot] Stop signal received during pre-join for meeting #{meeting_id}.")
                return roster_participants, speech_segments, False
            time.sleep(1)

            try:
                # 1. Handle launcher screen ("Continue on this browser")
                for sel in [
                    'button[data-tid="joinOnWeb"]',
                    'button:has-text("Continue on this browser")',
                    'button:has-text("Join on the web instead")',
                    'button:has-text("Continue without audio or video")'
                ]:
                    try:
                        b = page.locator(sel)
                        if b.count() > 0 and b.first.is_visible(timeout=300):
                            logger.info(f"[Teams Bot] Clicking launcher button '{sel}'...")
                            b.first.click()
                            time.sleep(3)
                            break
                    except Exception:
                        pass

                # 2. Fill Display Name if on prejoin screen
                name_input = page.locator(
                    'input[data-tid="prejoin-display-name-input"], '
                    'input#username, '
                    'input[name="username"], '
                    'input[aria-label*="name" i], '
                    'input[placeholder*="name" i], '
                    'input[placeholder*="Enter name" i]'
                )
                if name_input.count() > 0 and name_input.first.is_visible(timeout=300):
                    val = name_input.first.input_value()
                    if not val or val.strip() == "":
                        logger.info(f"[Teams Bot] Entering display name: '{bot_name}'")
                        name_input.first.click()
                        name_input.first.fill(bot_name)
                        name_input.first.press("Tab")
                        time.sleep(0.5)

                # 3. Mute mic if unmuted
                for sel in [
                    'button[data-tid="toggle-mute"][aria-checked="false"]',
                    'button[aria-label*="Unmuted" i]',
                    'button[aria-label*="Turn off microphone" i]'
                ]:
                    try:
                        b = page.locator(sel)
                        if b.count() > 0 and b.first.is_visible(timeout=200):
                            b.first.click()
                            time.sleep(0.2)
                            break
                    except Exception:
                        pass

                # 4. Mute camera if turned on
                for sel in [
                    'button[data-tid="toggle-video"][aria-checked="true"]',
                    'button[aria-label*="Camera on" i]',
                    'button[aria-label*="Turn off camera" i]'
                ]:
                    try:
                        b = page.locator(sel)
                        if b.count() > 0 and b.first.is_visible(timeout=200):
                            b.first.click()
                            time.sleep(0.2)
                            break
                    except Exception:
                        pass

                # 5. Click Join Button once name is entered
                if not join_clicked:
                    for join_sel in [
                        'button[data-tid="prejoin-join-button"]',
                        'button:has-text("Join now")',
                        'button:has-text("Join meeting")',
                        'button:has-text("Join")'
                    ]:
                        try:
                            jb = page.locator(join_sel)
                            if jb.count() > 0 and jb.first.is_visible(timeout=300):
                                is_disabled = jb.first.get_attribute("disabled") is not None
                                if not is_disabled:
                                    logger.info(f"[Teams Bot] Clicking Join Button ({join_sel})...")
                                    jb.first.click()
                                    join_clicked = True
                                    time.sleep(3)
                                    break
                        except Exception:
                            pass

                # 6. Check if in lobby or already entered call
                body_text = ""
                try:
                    body_text = page.locator("body").inner_text(timeout=500).lower()
                except Exception:
                    pass

                lobby_phrases = [
                    "someone will let you in shortly",
                    "let you in shortly",
                    "waiting in the lobby",
                    "let you in soon",
                    "waiting for host",
                    "we'll let people know you're waiting",
                    "someone in the meeting should let you in",
                    "waiting to be admitted"
                ]
                if any(p in body_text for p in lobby_phrases):
                    in_lobby = True
                    logger.info(f"[Teams Bot] Successfully submitted join request! Waiting in lobby for meeting #{meeting_id}...")
                    break

                # If leave button or in-call controls appear right away
                for l_chk in ['button:has-text("Leave")', 'button:has-text("Hang up")', 'button[data-tid="hangup-button"]', 'button[aria-label*="Leave" i]']:
                    if page.locator(l_chk).count() > 0:
                        in_call = True
                        has_ever_joined_call = True
                        logger.info(f"[Teams Bot] Successfully entered live call for meeting #{meeting_id}!")
                        try:
                            execute_write("UPDATE scheduled_bot_jobs SET status = 'IN_CALL' WHERE meeting_id = %s", (meeting_id,))
                        except Exception:
                            pass
                        break
                if in_call:
                    break

            except Exception as step_err:
                logger.debug(f"[Teams Bot] Step note: {step_err}")

        # Step 2: Monitoring Loop (Lobby wait + In-Call Telemetry)
        logger.info(f"[Teams Bot] Beginning meeting monitoring loop for meeting #{meeting_id}...")
        last_lobby_log = time.time()
        last_debug_screenshot_ts = 0
        max_duration_seconds = 14400  # 4-hour safety ceiling

        while not stop_event.is_set():
            time.sleep(2)
            try:
                # 1. Enforce safety ceiling
                if (time.time() - start_ts) > max_duration_seconds:
                    logger.info(f"[Teams Bot] Maximum session duration (4h) reached for meeting #{meeting_id}. Concluding.")
                    completed_normally = True
                    break

                body_text = ""
                try:
                    body_text = page.locator("body").inner_text(timeout=500).lower()
                except Exception:
                    pass

                # 2. Check if still in lobby
                in_lobby_phrases = [
                    "someone will let you in shortly",
                    "let you in shortly",
                    "waiting in the lobby",
                    "let you in soon",
                    "we'll let people know you're waiting",
                    "someone in the meeting should let you in",
                    "waiting to be admitted"
                ]
                is_lobby_text = any(phrase in body_text for phrase in in_lobby_phrases)

                # 3. Check in-call controls and elements
                has_leave_btn = False
                for l_sel in [
                    'button:has-text("Leave")',
                    'button:has-text("Hang up")',
                    'button[data-tid="hangup-button"]',
                    'button[data-tid="call-end"]',
                    '#hangup-button',
                    'button[aria-label*="Leave" i]',
                    'button[aria-label*="Hang up" i]',
                    'button[aria-label*="Disconnect" i]'
                ]:
                    try:
                        if page.locator(l_sel).count() > 0:
                            has_leave_btn = True
                            break
                    except Exception:
                        pass

                has_controls = False
                for c_sel in [
                    'button[data-tid="calling-participants-button"]',
                    'button[data-tid="roster-button"]',
                    'button#roster-button',
                    'button[aria-label*="Show participants" i]',
                    'button[aria-label*="People" i]',
                    'button:has-text("People")',
                    'button[data-tid="chat-button"]'
                ]:
                    try:
                        if page.locator(c_sel).count() > 0:
                            has_controls = True
                            break
                    except Exception:
                        pass

                # Accurate in-call determination:
                # Must NOT have lobby text or join button, and MUST have real in-call leave/hangup button or calling toolbar
                if is_lobby_text or "join now" in body_text or "someone will let you in shortly" in body_text:
                    in_call = False
                    in_lobby = True
                elif has_leave_btn or (has_controls and "join" not in body_text):
                    if not in_call:
                        in_call = True
                        in_call_start_ts = time.time()
                        has_ever_joined_call = True
                        in_lobby = False
                        logger.info(f"[Teams Bot] Bot has been admitted into live call for meeting #{meeting_id}!")
                        try:
                            execute_write("UPDATE scheduled_bot_jobs SET status = 'IN_CALL' WHERE meeting_id = %s", (meeting_id,))
                        except Exception:
                            pass

                # 4. If in call, continuously ensure People panel is open to reveal participants
                if in_call:
                    is_roster_open = False
                    try:
                        is_roster_open = page.evaluate("""
                            () => {
                                const text = document.body.innerText || '';
                                return text.includes('In this meeting') || text.includes('Participants');
                            }
                        """)
                    except Exception:
                        pass
                    if not is_roster_open:
                        for r_sel in [
                            'button[data-tid="calling-participants-button"]',
                            'button#roster-button',
                            'button[data-tid="roster-button"]',
                            'button[aria-label*="Show participants" i]',
                            'button[aria-label*="People" i]',
                            'button[aria-label*="participants" i]',
                            'button:has-text("People")'
                        ]:
                            try:
                                rb = page.locator(r_sel)
                                if rb.count() > 0 and rb.first.is_visible(timeout=300):
                                    rb.first.click()
                                    roster_panel_opened = True
                                    logger.info(f"[Teams Bot] Opened People panel to monitor participants for meeting #{meeting_id}.")
                                    time.sleep(1)
                                    break
                            except Exception:
                                pass

                # 5. If in call, continuously ensure live captions are autonomously activated
                now_ts = time.time()
                # Periodic debug screenshot so we can verify exact UI state
                if in_call and (now_ts - last_debug_screenshot_ts > 10):
                    last_debug_screenshot_ts = now_ts
                    try:
                        page.screenshot(path="scratch/bot_current_view.png")
                    except Exception:
                        pass

                # Check if captions container exists and is visible in the DOM
                has_captions_dom = False
                try:
                    has_captions_dom = page.evaluate("""
                        () => {
                            const el = document.querySelector(
                                '[data-tid="closed-caption-v2-virtual-list-content"], ' +
                                '[data-tid="closed-caption-chat-message"], ' +
                                '[data-tid="closed-captions-container"], ' +
                                '[data-tid="closed-captions-renderer"], ' +
                                '[data-tid="closed-caption-renderer-wrapper"], ' +
                                'div[class*="closedCaptions"], ' +
                                'div[class*="captionContainer"]'
                            );
                            return !!(el && el.offsetParent !== null);
                        }
                    """)
                except Exception:
                    pass

                if has_captions_dom:
                    captions_enabled = True

                # If captions container not visible, attempt activation
                if in_call and not captions_enabled and (now_ts - last_caption_toggle_ts > 12):
                    last_caption_toggle_ts = now_ts
                    try:
                        # Wiggle mouse near top bar (where More button lives)
                        page.mouse.move(800, 25)
                        time.sleep(0.3)

                        # Find and click More actions button
                        more_btn = None
                        for more_sel in [
                            'button[aria-label*="More actions" i]',
                            'button[aria-label*="More" i]',
                            'button:has-text("More")',
                            'button[data-tid*="more-actions"]',
                            'button#callingButtons-showMoreBtn',
                            'button[id*="more-button"]',
                            'button[data-tid="overflow-button"]'
                        ]:
                            try:
                                b = page.locator(more_sel)
                                if b.count() > 0:
                                    more_btn = b.first
                                    break
                            except Exception:
                                pass

                        if more_btn:
                            more_btn.click(force=True, timeout=1000)
                            time.sleep(0.8)
                            logger.info(f"[Teams Bot] Clicked More actions menu button for meeting #{meeting_id}.")

                            # Find Captions menu item
                            caption_btn = page.locator(
                                '[role="menuitem"], [role="menuitemcheckbox"], .fui-MenuItem, button'
                            ).filter(has_text=re.compile(r'Captions', re.I)).first

                            if caption_btn.count() > 0:
                                is_checked = caption_btn.evaluate("""el => {
                                    return el.getAttribute('aria-checked') === 'true' || 
                                           el.classList.contains('fui-MenuItem--checked') ||
                                           !!el.querySelector('[data-icon-name="Checkmark"], [class*="checkmark"], svg[data-icon-name*="check" i]');
                                }""")
                                if not is_checked:
                                    caption_btn.click(force=True, timeout=1000)
                                    captions_enabled = True
                                    logger.info(f"[Teams Bot] Native clicked 'Captions' to enable for meeting #{meeting_id}!")
                                    time.sleep(0.8)
                                else:
                                    captions_enabled = True
                                    logger.info(f"[Teams Bot] 'Captions' already checked for meeting #{meeting_id}.")
                                page.keyboard.press("Escape")
                                time.sleep(0.3)
                            else:
                                logger.info(f"[Teams Bot] Captions menu item not found in More menu.")
                                page.keyboard.press("Escape")
                        else:
                            logger.warning(f"[Teams Bot] More actions button not found for meeting #{meeting_id}.")

                        # Spoken language confirmation dialog if shown
                        for confirm_sel in [
                            'button:has-text("Confirm")',
                            'button:has-text("Done")',
                            'button:has-text("Save")',
                            'button:has-text("Turn on")',
                            'button:has-text("Got it")',
                            'button[data-tid="confirm-spoken-language-button"]'
                        ]:
                            try:
                                c_btn = page.locator(confirm_sel)
                                if c_btn.count() > 0 and c_btn.first.is_visible(timeout=300):
                                    c_btn.click(force=True)
                                    captions_enabled = True
                                    logger.info(f"[Teams Bot] Confirmed spoken language dialog.")
                                    time.sleep(0.5)
                            except Exception:
                                pass

                    except Exception as cap_err:
                        logger.info(f"[Teams Bot] Caption toggle note: {cap_err}")

                        # Fallback keyboard shortcuts
                        if not captions_enabled:
                            try:
                                page.locator('body').focus()
                                page.keyboard.press("Control+Shift+C")
                                time.sleep(0.2)
                                page.keyboard.press("Alt+Shift+C")
                            except Exception:
                                pass

                    except Exception as cap_err:
                        logger.debug(f"[Teams Bot] Caption toggle note: {cap_err}")

                # Keep Teams awake and prevent toolbar auto-hiding
                if in_call and (now_ts - last_nudge_ts > 10):
                    last_nudge_ts = now_ts
                    try:
                        page.mouse.move(500, 300)
                    except Exception:
                        pass

                # 6. If still in lobby, log periodic heartbeat & enforce lobby timeout
                if not in_call and (in_lobby or is_lobby_text):
                    in_lobby = True
                    if time.time() - last_lobby_log > 30:
                        logger.info(f"[Teams Bot] Bot is waiting in lobby to be admitted for meeting #{meeting_id}...")
                        last_lobby_log = time.time()
                    if (time.time() - start_ts) > 900:  # 15 min lobby timeout
                        logger.warning(f"[Teams Bot] Bot was not admitted from lobby after 15 minutes for meeting #{meeting_id}. Aborting.")
                        completed_normally = False
                        try:
                            execute_write("UPDATE scheduled_bot_jobs SET status = 'FAILED', error_message = 'Lobby wait timeout (not admitted by host)' WHERE meeting_id = %s", (meeting_id,))
                        except Exception:
                            pass
                        break

                # 7. Check if host explicitly denied bot in lobby
                lobby_denial_phrases = [
                    "denied your request to join",
                    "denied entry",
                    "request to join was declined",
                    "you can't join this meeting",
                    "couldn't let you in",
                    "removed from the lobby"
                ]
                if not (in_call or has_ever_joined_call) and any(phrase in body_text for phrase in lobby_denial_phrases):
                    logger.warning(f"[Teams Bot] Host denied bot admission in lobby for meeting #{meeting_id}.")
                    completed_normally = False
                    try:
                        execute_write("UPDATE scheduled_bot_jobs SET status = 'FAILED', error_message = 'Host denied bot entry in lobby' WHERE meeting_id = %s", (meeting_id,))
                    except Exception:
                        pass
                    break

                # 8. Check if meeting concluded or host removed bot from meeting
                conclusion_phrases = [
                    "the meeting has ended",
                    "this meeting has ended",
                    "the call has ended",
                    "you left the meeting",
                    "you've been removed",
                    "you were removed from the meeting",
                    "someone removed you from the meeting",
                    "someone removed you",
                    "you were removed",
                    "removed from the meeting",
                    "meeting ended",
                    "the host ended the meeting",
                    "the organizer ended the meeting",
                    "the meeting has ended for everyone",
                    "no longer in the meeting",
                    "return to home screen",
                    "how was the call quality",
                    "rate your call quality",
                    "rate this call",
                    "you left the call",
                    "rejoin"
                ]
                if any(phrase in body_text for phrase in conclusion_phrases):
                    if in_call or has_ever_joined_call:
                        logger.info(f"[Teams Bot] Meeting #{meeting_id} conclusion detected via UI message: {[p for p in conclusion_phrases if p in body_text]}")
                        completed_normally = True
                    else:
                        logger.info(f"[Teams Bot] Meeting #{meeting_id} ended before admission.")
                        completed_normally = False
                    break

                # 8a. Rejoin / Dismiss prompt detection (shown when removed by host or meeting concludes)
                has_rejoin_prompt = False
                for rej_sel in ['button:has-text("Rejoin")', 'button[data-tid="rejoin-button"]', 'button:has-text("Dismiss")']:
                    try:
                        r_el = page.locator(rej_sel)
                        if r_el.count() > 0 and r_el.first.is_visible(timeout=100):
                            has_rejoin_prompt = True
                            break
                    except Exception:
                        pass
                if has_rejoin_prompt and (in_call or has_ever_joined_call):
                    logger.info(f"[Teams Bot] Rejoin / Dismiss dialog visible for meeting #{meeting_id}. Host removed bot or call concluded.")
                    completed_normally = True
                    break

                # 8b. Disconnection detection: If in-call and calling toolbar disappears
                if in_call and not has_leave_btn and not has_controls:
                    # Teams auto-hides the control bar when mouse is inactive. Wiggle mouse.
                    try:
                        page.mouse.move(600, 400)
                    except Exception:
                        pass
                    controls_missing_count += 1
                    # Only conclude if controls missing for >= 60 consecutive checks (~120s)
                    if controls_missing_count >= 60:
                        logger.info(f"[Teams Bot] In-call controls disappeared for >2m for meeting #{meeting_id}. Meeting concluded or ended by host.")
                        completed_normally = True
                        break
                else:
                    controls_missing_count = 0

                # 8c. Alone detection: If in call for >30s and all human attendees have left
                if in_call and (time.time() - in_call_start_ts) >= 30:
                    is_alone = any(p in body_text for p in [
                        "you're the only one here",
                        "nobody else is here"
                    ])
                    # Only conclude alone if no human roster participants were ever discovered
                    if is_alone and len(roster_participants) == 0:
                        alone_consecutive_count += 1
                        if alone_consecutive_count >= 15:  # ~30 consecutive seconds
                            logger.info(f"[Teams Bot] All human participants left meeting #{meeting_id}. Concluding session.")
                            completed_normally = True
                            break
                    else:
                        alone_consecutive_count = 0

                # 9. Scrape participants if in call (sidebar roster + video stage tiles)
                if in_call:
                    try:
                        roster_data = page.evaluate("""
                            () => {
                                const list = [];
                                const seen = new Set();
                                const botName = "ai meeting facilitator";

                                function addName(rawName) {
                                    if (!rawName) return;
                                    let name = rawName.trim();
                                    if (name.includes('\\n')) {
                                        name = name.split('\\n')[0].trim();
                                    }
                                    name = name.replace(/\\s*\\((Organiser|Organizer|Presenter|Attendee|Guest|External|You|Me|IN|TPR)\\)/gi, '').trim();

                                    if (/\\d/.test(name)) return;
                                    if (/message|status|encrypt|setting|action|invite|search|meeting|record|caption|audio|video|device|roster|privacy|policy|leave|raise|react|people|presenter|call me|chat bubbles|gallery|full screen|background|format|emoji|send|discard|insert|link|unmuted|muted|camera|share|participants|options/i.test(name)) {
                                        return;
                                    }
                                    if (name.length < 3 || name.length > 40) return;
                                    const lower = name.toLowerCase();
                                    if (lower === botName || seen.has(lower)) return;
                                    seen.add(lower);
                                    list.push(name);
                                }

                                // 1. Check inside Participants / In this meeting sidebar
                                const candidatePanels = Array.from(document.querySelectorAll('div, aside, section, [role="complementary"]')).filter(el => {
                                    const text = el.innerText || '';
                                    return (text.includes('Participants') || text.includes('In this meeting')) && !el.closest('[role="menu"], .fui-MenuPopover');
                                });
                                for (const panel of candidatePanels) {
                                    panel.querySelectorAll('[role="treeitem"], [role="listitem"], div[class*="participant"], div[class*="roster"], [data-tid*="roster-item"]').forEach(el => {
                                        if (el.closest('[role="menu"], .fui-MenuPopover, [role="menubar"], nav')) return;
                                        const nameEl = el.querySelector('[data-tid*="name"], span[dir="auto"], span[class*="name"], div[class*="name"]') || el;
                                        let txt = (nameEl.innerText || '').trim();
                                        if (txt) {
                                            if (txt.includes('\\n')) txt = txt.split('\\n')[0].trim();
                                            addName(txt);
                                        }
                                    });
                                }

                                // 2. Check video stage tiles and name badges
                                document.querySelectorAll(
                                    '[data-tid*="stream"], [data-tid*="participant"], [data-tid*="video-tile"], ' +
                                    'div[class*="tile"], span[class*="name"], div[class*="name"], div[class*="badge"], ' +
                                    'div[id*="tile-name"], [data-tid*="tile-name"], [data-tid*="name-badge"]'
                                ).forEach(el => {
                                    if (el.closest('[role="menu"], .fui-MenuPopover, [role="menubar"], nav, button, footer')) return;
                                    let text = (el.innerText || "").trim();
                                    if (text.includes('\\n')) text = text.split('\\n')[0].trim();
                                    if (!text || text.length < 3) {
                                        text = (el.getAttribute('title') || '').split(',')[0].trim();
                                    }
                                    addName(text);
                                });

                                return list;
                            }
                        """)
                        for r_name in (roster_data or []):
                            clean_n = r_name
                            for att_n in (known_attendees or []):
                                if att_n.lower() in clean_n.lower() or clean_n.lower() in att_n.lower():
                                    clean_n = att_n
                                    break
                            if clean_n not in seen_names and clean_n.lower() not in SYSTEM_UI_WORDS and clean_n.lower() != bot_name.lower():
                                seen_names.add(clean_n)
                                roster_participants.append({
                                    "name": clean_n,
                                    "join_time": datetime.now(timezone.utc).isoformat()
                                })
                                logger.info(f"[Teams Bot] Discovered active participant: {clean_n}")
                    except Exception as r_err:
                        logger.debug(f"[Teams Bot] Roster scrape note: {r_err}")

                # 10. Scrape live captions if in call and persist in real-time (Priority 1 speech capture)
                if in_call:
                    try:
                        raw_captions = page.evaluate("""
                            () => {
                                const items = [];
                                const seenTexts = new Set();

                                function recordCaption(rawSpeaker, rawText) {
                                    if (!rawText) return;
                                    let text = rawText.trim();
                                    let speaker = (rawSpeaker || 'Participant').trim();
                                    
                                    // Remove avatar noise or single letters
                                    if (/^[A-Z0-9]{1,3}$/.test(speaker)) speaker = 'Participant';
                                    
                                    // Ensure text is not identical to speaker name
                                    if (text.toLowerCase() === speaker.toLowerCase()) return;
                                    if (text.length < 2) return;
                                    
                                    const key = speaker.toLowerCase() + ":::" + text.toLowerCase();
                                    if (seenTexts.has(key)) return;
                                    seenTexts.add(key);
                                    items.push({ speaker: speaker, text: text });
                                }

                                // Strategy 1: Dedicated closed-caption message cards
                                const chatCards = document.querySelectorAll(
                                    '[data-tid="closed-caption-chat-message"], ' +
                                    '[data-tid="closed-caption-v2-virtual-list-content"] > div, ' +
                                    'div[class*="closed-caption-chat-message"], ' +
                                    'div[data-tid="caption-item"]'
                                );
                                for (const card of chatCards) {
                                    let speaker = '';
                                    let text = '';
                                    const authorEl = card.querySelector('.ui-chat__message__author, [class*="author"], [data-tid*="author"], span[dir="auto"], strong');
                                    if (authorEl) speaker = (authorEl.innerText || '').trim();
                                    const textEl = card.querySelector('[data-tid="closed-caption-text"], [class*="caption-text"]');
                                    if (textEl) text = (textEl.innerText || '').trim();

                                    if (!speaker || !text) {
                                        const rawLines = (card.innerText || '').split('\\n').map(l => l.trim()).filter(l => l && !/^[A-Z]{1,3}$/.test(l));
                                        if (rawLines.length >= 2) {
                                            if (!speaker) speaker = rawLines[0];
                                            if (!text) text = rawLines.slice(1).join(' ');
                                        } else if (rawLines.length === 1 && !text) {
                                            text = rawLines[0];
                                        }
                                    }
                                    recordCaption(speaker, text);
                                }

                                // Strategy 2: Direct lookup of [data-tid="closed-caption-text"]
                                if (items.length === 0) {
                                    const textEls = document.querySelectorAll('[data-tid="closed-caption-text"]');
                                    for (const tel of textEls) {
                                        const txt = (tel.innerText || '').trim();
                                        if (!txt) continue;
                                        const parent = tel.closest('[data-tid="closed-caption-chat-message"], div[class*="caption"], div') || tel.parentElement;
                                        let spk = 'Participant';
                                        if (parent) {
                                            const spkEl = parent.querySelector('.ui-chat__message__author, [class*="author"], [data-tid*="author"], span[dir="auto"], strong');
                                            if (spkEl) spk = (spkEl.innerText || '').trim();
                                        }
                                        recordCaption(spk, txt);
                                    }
                                }

                                // Strategy 3: Bottom Subtitle Container (Shown in Teams Web / Image 4 & 5)
                                if (items.length === 0) {
                                    const containers = Array.from(document.querySelectorAll(
                                        '[data-tid="closed-captions-renderer"], ' +
                                        '[data-tid="closed-caption-renderer-wrapper"], ' +
                                        '[data-tid="closed-captions-container"], ' +
                                        '[data-tid="closed-caption-v2-virtual-list-content"], ' +
                                        '[data-tid*="caption"], ' +
                                        'div[class*="closedCaptions"], ' +
                                        'div[class*="closedCaption"], ' +
                                        'div[class*="captionContainer"], ' +
                                        'div[class*="caption-container"], ' +
                                        'div[class*="closed-caption"], ' +
                                        'div[aria-label*="caption" i], ' +
                                        'section[aria-label*="caption" i]'
                                    ));

                                    // Also locate caption container by settings or close buttons
                                    const captionCloseBtn = document.querySelector(
                                        'button[aria-label*="Turn off live captions" i], ' +
                                        'button[aria-label*="caption settings" i], ' +
                                        'button[data-tid*="caption-settings"]'
                                    );
                                    if (captionCloseBtn) {
                                        const bar = captionCloseBtn.closest('div[style*="position"], div[class*="caption"], div[class*="renderer"], div');
                                        if (bar && !containers.includes(bar)) containers.push(bar);
                                    }

                                    for (const cont of containers) {
                                        // First try child message cards inside container
                                        const cards = cont.querySelectorAll(':scope > div, div[class*="item"], div[class*="card"], div[class*="message"], div[class*="row"]');
                                        let parsedCards = false;
                                        if (cards && cards.length > 0) {
                                            for (const c of cards) {
                                                const cLines = (c.innerText || '').split('\\n')
                                                    .map(l => l.trim())
                                                    .filter(l => l && !/^[A-Z0-9]{1,3}$/.test(l) && !/Turn off|Settings|Dismiss/i.test(l));
                                                if (cLines.length >= 2) {
                                                    recordCaption(cLines[0], cLines.slice(1).join(' '));
                                                    parsedCards = true;
                                                }
                                            }
                                        }

                                        if (!parsedCards) {
                                            const raw = (cont.innerText || '').trim();
                                            if (!raw) continue;
                                            // Split lines and filter out avatar initials (e.g. 'RT', 'DS') and buttons ('Settings', 'Turn off', 'X')
                                            const lines = raw.split('\\n')
                                                .map(l => l.trim())
                                                .filter(l => l && !/^[A-Z0-9]{1,3}$/.test(l) && !/Turn off|Settings|Dismiss/i.test(l));
                                            
                                            // In Teams bottom bar, lines alternate between Speaker Name and Speech Text!
                                            for (let i = 0; i < lines.length; i += 2) {
                                                const spk = lines[i];
                                                const msg = (i + 1 < lines.length) ? lines[i + 1] : '';
                                                if (msg) {
                                                    recordCaption(spk, msg);
                                                } else if (spk && lines.length === 1) {
                                                    recordCaption('Participant', spk);
                                                }
                                            }
                                        }
                                    }
                                }

                                // Strategy 4: Live aria regions (aria-live="polite", aria-live="assertive")
                                if (items.length === 0) {
                                    const liveEls = document.querySelectorAll('div[aria-live="polite"] span, div[aria-live="assertive"] span, [role="log"]');
                                    for (const le of liveEls) {
                                        const txt = (le.innerText || '').trim();
                                        if (txt && txt.length > 2 && !/Turn off|Settings|Dismiss/i.test(txt)) {
                                            recordCaption('Participant', txt);
                                        }
                                    }
                                }

                                return items;
                            }
                        """)

                        if raw_captions:
                            now_sec = round(time.time() - start_ts, 1)
                            for cap_item in raw_captions:
                                raw_spk = (cap_item.get("speaker") or "Participant").strip()
                                speech_txt = (cap_item.get("text") or "").strip()
                                if not speech_txt:
                                    continue

                                # Unique key for de-duplicating seen captions
                                cap_key = f"{raw_spk.lower()}:{speech_txt.lower()}"
                                if cap_key in seen_captions:
                                    continue
                                seen_captions.add(cap_key)

                                # Normalize speaker name
                                clean_speaker = re.sub(
                                    r'\s*\((?:IN|TPR|Guest|Organizer|Organiser|Presenter|Attendee|You|Me)\)',
                                    '', raw_spk, flags=re.I
                                ).strip()

                                # Match with known attendees (e.g. "Deepak Saha", "Reetick Maity")
                                for att_n in (known_attendees or []):
                                    if att_n.lower() in clean_speaker.lower() or clean_speaker.lower() in att_n.lower():
                                        clean_speaker = att_n
                                        break
                                speaker = clean_speaker or "Participant"

                                # Register speaker in roster so attendance is marked present
                                if speaker not in seen_names and speaker.lower() not in SYSTEM_UI_WORDS and speaker.lower() != bot_name.lower():
                                    seen_names.add(speaker)
                                    roster_participants.append({
                                        "name": speaker,
                                        "join_time": datetime.now(timezone.utc).isoformat()
                                    })
                                    logger.info(f"[Teams Bot] Verified active speaker for attendance: {speaker}")

                                seg = {
                                    "speaker_name": speaker,
                                    "text": speech_txt,
                                    "start_seconds": now_sec,
                                    "end_seconds": now_sec + 3.0
                                }
                                speech_segments.append(seg)

                                # Real-time database persistence
                                try:
                                    execute_write(
                                        """INSERT INTO meeting_transcripts (meeting_id, speaker_name, text, start_seconds, end_seconds)
                                           VALUES (%s, %s, %s, %s, %s)""",
                                        (meeting_id, speaker, speech_txt, now_sec, now_sec + 3.0)
                                    )
                                    logger.info(f"[Live Captions] #{meeting_id} [{speaker}]: \"{speech_txt[:60]}\"")
                                except Exception as db_err:
                                    logger.debug(f"Live transcript insert error: {db_err}")

                    except Exception as cap_err:
                        logger.debug(f"[Teams Bot] Caption scrape error: {cap_err}")

            except Exception as loop_err:
                logger.debug(f"[Teams Bot] Telemetry loop error: {loop_err}")

        call_duration = time.time() - start_ts
        if (in_call or has_ever_joined_call) and (completed_normally or call_duration >= 30 or len(speech_segments) > 0 or len(roster_participants) > 0):
            completed_normally = True

        return roster_participants, speech_segments, completed_normally

    @classmethod
    def _run_google_meet_session(
        cls,
        page,
        meeting_id: int,
        stop_event: threading.Event,
        roster_participants: Optional[List[Dict[str, Any]]] = None,
        speech_segments: Optional[List[Dict[str, Any]]] = None
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], bool]:
        """Automates Google Meet join, people panel scraping, and captions."""
        logger.info(f"[Google Meet Bot] Starting session for meeting #{meeting_id}...")
        bot_name = Config.BOT_NAME
        if roster_participants is None:
            roster_participants = []
        if speech_segments is None:
            speech_segments = []
        seen_captions: Set[str] = set()
        seen_names: Set[str] = set()
        start_ts = time.time()
        in_call = False
        completed_normally = False
        captions_enabled = False
        people_opened = False

        # Step 1: Pre-join (wait up to 30s for prejoin controls)
        for _ in range(15):
            if stop_event.is_set():
                return roster_participants, speech_segments, False
            time.sleep(1)
            try:
                name_input = page.locator('input[aria-label="Your name"], input[placeholder="Your name"]')
                if name_input.count() > 0 and name_input.first.is_visible(timeout=300):
                    name_input.first.fill(bot_name)
                    time.sleep(0.5)

                # Mute camera & mic
                page.keyboard.press("Control+e")
                time.sleep(0.2)
                page.keyboard.press("Control+d")
                time.sleep(0.3)

                # Click Join / Ask to join
                join_btn = page.locator('button:has-text("Ask to join"), button:has-text("Join now")')
                if join_btn.count() > 0 and join_btn.first.is_visible(timeout=300):
                    join_btn.first.click()
                    time.sleep(3)
                    # Check if directly in call or waiting to be let in
                    leave_chk = page.locator('button[aria-label*="Leave call" i]')
                    if leave_chk.count() > 0 and leave_chk.first.is_visible(timeout=500):
                        in_call = True
                        execute_write("UPDATE scheduled_bot_jobs SET status = 'IN_CALL' WHERE meeting_id = %s", (meeting_id,))
                    break
            except Exception as prejoin_err:
                logger.debug(f"[Google Meet Bot] Prejoin step note: {prejoin_err}")

        # Step 2: Monitoring Loop
        logger.info(f"[Google Meet Bot] Entering monitoring loop for meeting #{meeting_id}...")
        max_duration_seconds = 14400

        while not stop_event.is_set():
            time.sleep(2)
            try:
                if (time.time() - start_ts) > max_duration_seconds:
                    logger.info(f"[Google Meet Bot] Maximum duration reached for meeting #{meeting_id}.")
                    completed_normally = True
                    break

                body_text = page.locator("body").inner_text(timeout=500).lower()
                if any(p in body_text for p in [
                    "you left the meeting",
                    "the meeting ended",
                    "return to home screen",
                    "you've been removed"
                ]):
                    if in_call:
                        logger.info(f"[Google Meet Bot] Meeting #{meeting_id} ended.")
                        completed_normally = True
                    else:
                        logger.warning(f"[Google Meet Bot] Host denied bot entry before entering call for meeting #{meeting_id}.")
                        completed_normally = False
                    break

                # Check if in call controls appeared
                leave_btn = page.locator('button[aria-label*="Leave call" i]')
                if leave_btn.count() > 0:
                    if not in_call:
                        in_call = True
                        try:
                            execute_write("UPDATE scheduled_bot_jobs SET status = 'IN_CALL' WHERE meeting_id = %s", (meeting_id,))
                        except Exception:
                            pass

                # Turn on Captions: Try 3 dots (More options) -> 'Captions', or toolbar CC button, or 'c'
                if in_call and not captions_enabled:
                    try:
                        # 1. Try 3 dots (More options)
                        more_btn = None
                        for m_sel in [
                            'button[aria-label*="More options" i]',
                            'button[data-tooltip*="More options" i]',
                            'button[aria-label*="More actions" i]',
                            'button[jsname="A5il2e"]'
                        ]:
                            b = page.locator(m_sel)
                            if b.count() > 0 and b.first.is_visible(timeout=200):
                                more_btn = b.first
                                break

                        if more_btn:
                            more_btn.click(timeout=500)
                            time.sleep(0.5)
                            cap_item = page.locator('[role="menuitem"]:has-text("Captions"), [role="menuitem"]:has-text("Turn on captions"), span:has-text("Captions")')
                            if cap_item.count() > 0 and cap_item.first.is_visible(timeout=300):
                                cap_item.first.click()
                                captions_enabled = True
                                logger.info(f"[Google Meet Bot] Clicked 'Captions' in 3-dots menu for meeting #{meeting_id}.")
                                time.sleep(0.5)
                            else:
                                page.keyboard.press("Escape")

                        # 2. Direct toolbar CC button fallback
                        if not captions_enabled:
                            for cc_sel in [
                                'button[aria-label*="Turn on captions" i]',
                                'button[data-tooltip*="Turn on captions" i]',
                                'button[aria-label*="captions" i]',
                                'button[jsname="r8qRAd"]'
                            ]:
                                cb = page.locator(cc_sel)
                                if cb.count() > 0 and cb.first.is_visible(timeout=200):
                                    cb.first.click()
                                    captions_enabled = True
                                    logger.info(f"[Google Meet Bot] Clicked toolbar CC button for meeting #{meeting_id}.")
                                    break

                        # 3. Keyboard shortcut 'c' fallback
                        if not captions_enabled:
                            page.locator("body").focus()
                            page.keyboard.press("c")
                            captions_enabled = True
                            logger.info(f"[Google Meet Bot] Sent 'c' to enable live captions for meeting #{meeting_id}.")

                        # 4. Spoken language dialog confirmation if shown
                        for cf_sel in ['button:has-text("Apply")', 'button:has-text("Turn on")', 'button:has-text("Done")']:
                            cf = page.locator(cf_sel)
                            if cf.count() > 0 and cf.first.is_visible(timeout=200):
                                cf.first.click()
                                break
                    except Exception as cap_err:
                        logger.debug(f"[Google Meet Bot] Caption toggle note: {cap_err}")

                # Open people panel once in call
                if in_call and not people_opened:
                    try:
                        people_btn = page.locator('button[aria-label*="People" i], button[aria-label*="participants" i]')
                        if people_btn.count() > 0 and people_btn.first.is_visible(timeout=300):
                            people_btn.first.click()
                            people_opened = True
                            time.sleep(1)
                    except Exception:
                        pass

                # Scrape participants
                if in_call:
                    p_items = page.locator('div[aria-label*="People" i] [role="listitem"], div[role="listitem"]')
                    for i in range(min(p_items.count(), 30)):
                        try:
                            p_el = p_items.nth(i)
                            span_el = p_el.locator('span').first
                            p_name = span_el.inner_text(timeout=100).strip() if span_el.count() > 0 else ""
                            if p_name and p_name != bot_name and len(p_name) > 2 and p_name not in seen_names:
                                seen_names.add(p_name)
                                roster_participants.append({
                                    "name": p_name,
                                    "join_time": datetime.now(timezone.utc).isoformat()
                                })
                                logger.info(f"[Google Meet Bot] Discovered participant: {p_name}")
                        except Exception:
                            pass

                # Scrape captions
                if in_call:
                    cap_containers = page.locator('div[jsname="YSgAZ"], div[class*="caption"], div[class*="Captions"], div[class*="bh44bd"]')
                    if cap_containers.count() > 0:
                        for k in range(cap_containers.count()):
                            try:
                                cap_el = cap_containers.nth(k)
                                cap_text = cap_el.inner_text(timeout=100).strip()
                                if cap_text and cap_text not in seen_captions:
                                    seen_captions.add(cap_text)
                                    now_sec = round(time.time() - start_ts, 1)

                                    speaker = "Participant"
                                    try:
                                        spk_el = cap_el.locator('span[class*="speaker"], div[class*="speaker"], div[class*="zsT0G"]')
                                        if spk_el.count() > 0:
                                            s_t = spk_el.first.inner_text(timeout=100).strip()
                                            if s_t:
                                                speaker = s_t
                                                if cap_text.startswith(s_t):
                                                    cap_text = cap_text[len(s_t):].lstrip(":").strip()
                                    except Exception:
                                        pass

                                    if speaker == "Participant" and ":" in cap_text:
                                        parts = cap_text.split(":", 1)
                                        if len(parts[0].strip()) < 40:
                                            speaker = parts[0].strip()
                                            cap_text = parts[1].strip()

                                    seg = {
                                        "speaker_name": speaker,
                                        "text": cap_text,
                                        "start_seconds": now_sec,
                                        "end_seconds": now_sec + 3.0
                                    }
                                    speech_segments.append(seg)

                                    try:
                                        execute_write(
                                            """INSERT INTO meeting_transcripts (meeting_id, speaker_name, text, start_seconds, end_seconds)
                                               VALUES (%s, %s, %s, %s, %s)""",
                                            (meeting_id, speaker, cap_text, now_sec, now_sec + 3.0)
                                        )
                                    except Exception as db_err:
                                        logger.debug(f"Live transcript insert error: {db_err}")

                                    logger.info(f"[Live Speech] #{meeting_id} {speaker}: \"{cap_text[:60]}\"")
                            except Exception:
                                pass

            except Exception as meet_err:
                logger.debug(f"[Google Meet Bot] Monitoring note: {meet_err}")

        if in_call and not stop_event.is_set():
            completed_normally = True

        return roster_participants, speech_segments, completed_normally
