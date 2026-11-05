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
        call_duration: float = 0.0
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
                    permissions=["microphone"],
                    args=[
                        "--use-fake-ui-for-media-stream",
                        "--use-fake-device-for-media-stream",
                        "--disable-blink-features=AutomationControlled",
                        "--no-sandbox",
                        "--disable-setuid-sandbox",
                        "--disable-notifications",
                        "--autoplay-policy=no-user-gesture-required",
                        "--disable-background-timer-throttling",
                        "--disable-backgrounding-occluded-windows",
                        "--disable-renderer-backgrounding",
                        "--disable-features=CalculateNativeWinOcclusion",
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
                    captured_roster, captured_speech, completed_normally, call_duration = cls._run_teams_session(
                        page, meeting_id, stop_event, captured_roster, captured_speech,
                        known_attendees=known_attendees
                    )
                elif "meet.google.com" in join_url:
                    captured_roster, captured_speech, completed_normally, call_duration = cls._run_google_meet_session(
                        page, meeting_id, stop_event, captured_roster, captured_speech
                    )
                else:
                    logger.warning(f"Unrecognized meeting URL: {join_url}, trying generic teams session...")
                    captured_roster, captured_speech, completed_normally, call_duration = cls._run_teams_session(
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
                logger.info(f"Triggering Post-Meeting Intelligence Pipeline for meeting #{meeting_id} (duration: {call_duration:.1f}s)...")
                try:
                    AnalysisEngine.run_post_meeting_pipeline(
                        meeting_id=meeting_id,
                        roster_participants=captured_roster,
                        speech_segments=captured_speech,
                        actual_duration_seconds=call_duration
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
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], bool, float]:
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
        captions_activated_once = False
        observer_installed = False
        last_caption_toggle_ts = 0.0
        last_nudge_ts = 0.0
        controls_missing_count = 0
        alone_consecutive_count = 0
        last_caption_received_ts = 0.0
        completed_utterance_hashes: Set[str] = set()
        current_utterance = {
            "speaker": None,
            "text": "",
            "db_id": None,
            "start_sec": 0.0
        }

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
                return roster_participants, speech_segments, False, 0.0
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

                # 3. Mute mic if unmuted (toggle switch or button)
                try:
                    m_switch = page.locator('input[data-tid="toggle-mute"]').first
                    if m_switch.count() > 0 and m_switch.is_checked():
                        m_switch.click(force=True)
                        time.sleep(0.2)
                except Exception:
                    pass

                for sel in [
                    'button[data-tid="toggle-mute"][aria-checked="false"]',
                    'button[aria-label*="Unmuted" i]',
                    'button[aria-label*="Turn off microphone" i]',
                    'button[aria-label*="Mute microphone" i]'
                ]:
                    try:
                        b = page.locator(sel)
                        if b.count() > 0 and b.first.is_visible(timeout=200):
                            b.first.click()
                            time.sleep(0.2)
                            break
                    except Exception:
                        pass

                # 4. Turn camera OFF so bot renders standard avatar tile instead of video
                try:
                    v_switch = page.locator('input[data-tid="toggle-video"]').first
                    if v_switch.count() > 0 and v_switch.is_checked():
                        v_switch.click(force=True)
                        logger.info(f"[Teams Bot] Turned camera switch OFF in prejoin for meeting #{meeting_id}.")
                        time.sleep(0.2)
                except Exception:
                    pass

                for sel in [
                    'button[data-tid="toggle-video"][aria-checked="true"]',
                    'button[aria-label*="Camera on" i]',
                    'button[aria-label*="Turn off camera" i]',
                    'button[aria-label*="Turn camera off" i]'
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
            time.sleep(1 if in_call else 2)
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
                    'button[data-tid="hangup-main-btn"]',
                    '[data-tid="hangup-split-btn"]',
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
                    'button[data-tid="chat-button"]',
                    'button[data-tid="microphone-button"]',
                    'button[data-tid="video-button"]',
                    'button[data-tid="share-button"]',
                    '[data-tid="calling-screen"]',
                    '[data-tid="call-layout"]'
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

                # Inject registered attendees into page context for speaker resolution
                if in_call and not observer_installed:
                    try:
                        page.evaluate("attendees => { window.__knownAttendeesList = attendees || []; }", known_attendees or [])
                        observer_installed = True
                        logger.info(f"[Teams Bot] Registered known attendees in page for meeting #{meeting_id}: {known_attendees}")
                    except Exception as obs_err:
                        logger.debug(f"[Teams Bot] Attendee registration note: {obs_err}")

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

                # 4b. Ensure camera remains OFF in call so tile displays standard user avatar
                if in_call:
                    try:
                        for cam_btn_sel in [
                            'button#callingButtons-cameraBtn[aria-checked="true"]',
                            'button[data-tid="toggle-video"][aria-checked="true"]',
                            'button[aria-label*="Turn off camera" i]',
                            'button[aria-label*="Turn camera off" i]'
                        ]:
                            cb = page.locator(cam_btn_sel)
                            if cb.count() > 0 and cb.first.is_visible(timeout=50):
                                cb.first.click(force=True)
                                logger.info(f"[Teams Bot] Turned camera OFF in-call for meeting #{meeting_id} to display standard user avatar.")
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
                                'div[class*="captionContainer"], ' +
                                'button[aria-label*="Turn off live captions" i], ' +
                                'button[aria-label*="caption settings" i], ' +
                                'button[data-tid*="caption-settings"]'
                            );
                            return !!(el && (el.offsetParent !== null || (el.getClientRects && el.getClientRects().length > 0)));
                        }
                    """)
                except Exception:
                    pass

                if has_captions_dom or (time.time() - last_caption_received_ts < 60) or len(speech_segments) > 0:
                    captions_enabled = True
                    captions_activated_once = True

                # If captions container not visible and never activated, attempt activation
                if in_call and not captions_activated_once and (now_ts - last_caption_toggle_ts > 10):
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
                                    captions_activated_once = True
                                    logger.info(f"[Teams Bot] Native clicked 'Captions' to enable for meeting #{meeting_id}!")
                                    time.sleep(0.8)
                                else:
                                    captions_enabled = True
                                    captions_activated_once = True
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
                                    captions_activated_once = True
                                    logger.info(f"[Teams Bot] Confirmed spoken language dialog.")
                                    time.sleep(0.5)
                            except Exception:
                                pass

                    except Exception as cap_err:
                        logger.info(f"[Teams Bot] Caption toggle note: {cap_err}")

                # Keep Teams awake and prevent toolbar auto-hiding
                if in_call and (now_ts - last_nudge_ts > 5):
                    last_nudge_ts = now_ts
                    try:
                        page.mouse.move(800, 30)
                    except Exception:
                        pass

                # 6. If still in lobby, log periodic heartbeat & enforce lobby timeout
                if not in_call and (in_lobby or is_lobby_text):
                    if has_ever_joined_call:
                        logger.info(f"[Teams Bot] Meeting #{meeting_id} already concluded (returned to lobby from live call). Concluding session normally.")
                        completed_normally = True
                        break
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
                # IMPORTANT: If leave button, calling controls, or captions were received recently, the call is ONGOING!
                is_call_active = has_leave_btn or has_controls or (time.time() - last_caption_received_ts < 60)
                if not is_call_active:
                    conclusion_phrases = [
                        "the meeting has ended for everyone",
                        "this meeting has ended",
                        "the call has ended",
                        "you were removed from the meeting",
                        "someone removed you from the meeting",
                        "the host ended the meeting",
                        "the organizer ended the meeting",
                        "you left the meeting",
                        "you left the call",
                        "no longer in the meeting"
                    ]
                    if any(phrase in body_text for phrase in conclusion_phrases):
                        if in_call or has_ever_joined_call:
                            logger.info(f"[Teams Bot] Meeting #{meeting_id} conclusion detected via UI message: {[p for p in conclusion_phrases if p in body_text]}")
                            completed_normally = True
                        else:
                            logger.info(f"[Teams Bot] Meeting #{meeting_id} ended before admission.")
                            completed_normally = False
                        break

                    # 8a. Auto-rejoin if unexpectedly disconnected with a Rejoin prompt while meeting is ongoing
                    if in_call or has_ever_joined_call:
                        rejoin_btn = page.locator('button:has-text("Rejoin"), button[data-tid="rejoin-button"]')
                        if rejoin_btn.count() > 0 and rejoin_btn.first.is_visible(timeout=200):
                            # If call ran for >60s and speech stopped >45s ago, meeting concluded normally
                            if (time.time() - in_call_start_ts) > 60 and (time.time() - last_caption_received_ts > 45):
                                logger.info(f"[Teams Bot] Rejoin screen appeared after meeting concluded for meeting #{meeting_id}. Concluding session normally.")
                                completed_normally = True
                                break
                            logger.info(f"[Teams Bot] Disconnection detected with Rejoin prompt for meeting #{meeting_id}. Auto-rejoining call immediately to avoid missing final captions...")
                            try:
                                rejoin_btn.first.click(force=True)
                                time.sleep(3)
                                in_call = True
                                controls_missing_count = 0
                                continue
                            except Exception as rej_err:
                                logger.debug(f"[Teams Bot] Rejoin click error: {rej_err}")

                # 8b. Disconnection detection: If in-call and calling toolbar disappears
                if in_call and not is_call_active:
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
                    # Conclude alone when all other participants have left the meeting
                    if is_alone:
                        alone_consecutive_count += 1
                        if alone_consecutive_count >= 8:  # ~16 consecutive seconds
                            logger.info(f"[Teams Bot] All human participants left meeting #{meeting_id}. Concluding session normally.")
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

                # 10. Extract live captions from active DOM and stream without duplication
                if in_call:
                    try:
                        clean_known_list = []
                        for k in (known_attendees or []):
                            cn = re.sub(r'\s*\(?(?:Organiser|Organizer|Presenter|Attendee|Guest|External|You|Me|IN|TPR)\)?', '', k, flags=re.I).strip()
                            cn = re.sub(r'\b(?:TPR|IN)\b', '', cn, flags=re.I).strip()
                            if cn and cn not in clean_known_list and not re.search(r'bot|facilitator', cn, re.I):
                                clean_known_list.append(cn)

                        raw_captions = page.evaluate("""
                            (known) => {
                                const results = [];

                                function cleanSpeaker(rawSpk) {
                                    if (!rawSpk) return '';
                                    let s = rawSpk.replace(/\\s*\\(?(?:Organiser|Organizer|Presenter|Attendee|Guest|External|You|Me|IN|TPR)\\)?/gi, '').trim();
                                    s = s.replace(/\\b(?:TPR|IN)\\b/g, '').trim();
                                    for (const name of (known || [])) {
                                        let cleanKnown = name.replace(/\\s*\\(?(?:Organiser|Organizer|Presenter|Attendee|Guest|External|You|Me|IN|TPR)\\)?/gi, '').trim();
                                        cleanKnown = cleanKnown.replace(/\\b(?:TPR|IN)\\b/g, '').trim();
                                        if (cleanKnown.toLowerCase() === s.toLowerCase() || 
                                            s.toLowerCase().includes(cleanKnown.toLowerCase()) || 
                                            cleanKnown.toLowerCase().includes(s.toLowerCase())) {
                                            return cleanKnown;
                                        }
                                    }
                                    if (/^[A-Z0-9]{1,3}$/.test(s)) return '';
                                    const INVALID = /^(icon|some|so|no|which|what|why|pipe|pipeline|before|like|from|there|tas|tasks|yes|yeah|just|it|the|a|an|and|or|in|on|at|to|for|with|by|about|status|action|meeting|caption|captions|participant|settings|dismiss|turn off)$/i;
                                    if (INVALID.test(s) || s.length < 3) return '';
                                    return s;
                                }

                                function cleanText(rawTxt) {
                                    if (!rawTxt) return '';
                                    return rawTxt.replace(/\\s+/g, ' ')
                                                 .replace(/^captions are turned on[\\.\\s]*/i, '')
                                                 .replace(/Turn off live captions|caption settings|Dismiss/gi, '')
                                                 .trim();
                                }

                                // 1. Locate caption container(s)
                                const containerSet = new Set();

                                const captionBtns = document.querySelectorAll(
                                    'button[aria-label*="caption" i], ' +
                                    'button[aria-label*="Turn off" i], ' +
                                    'button[data-tid*="caption-settings"], ' +
                                    'button[data-tid*="close-caption"]'
                                );
                                for (const b of captionBtns) {
                                    const box = b.closest('div[style*="position"], div[class*="caption"], div[class*="renderer"], div[role="region"], section') || b.parentElement?.parentElement;
                                    if (box) containerSet.add(box);
                                }

                                document.querySelectorAll(
                                    '[data-tid="closed-captions-renderer"], ' +
                                    '[data-tid="closed-caption-renderer-wrapper"], ' +
                                    '[data-tid="closed-captions-container"], ' +
                                    '[data-tid="closed-caption-v2-virtual-list-content"], ' +
                                    '[data-tid="closed-caption-chat-message"], ' +
                                    '[data-tid*="closed-caption"], ' +
                                    'div[class*="closedCaptions"], ' +
                                    'div[class*="closedCaption"], ' +
                                    'div[class*="captionContainer"], ' +
                                    'div[class*="caption-container"], ' +
                                    'div[aria-label*="caption" i], ' +
                                    'section[aria-label*="caption" i]'
                                ).forEach(el => {
                                    if (!el.closest('[role="menu"], .fui-MenuPopover, [role="menubar"], nav')) {
                                        containerSet.add(el);
                                    }
                                });

                                const containers = Array.from(containerSet);

                                // 2. First try discrete cards inside matching containers
                                for (const cont of containers) {
                                    const cards = cont.querySelectorAll(
                                        '[data-tid="closed-caption-chat-message"], ' +
                                        '[data-tid="caption-item"], ' +
                                        'div[class*="closed-caption-chat-message"], ' +
                                        'div[class*="caption-item"], ' +
                                        'div[class*="ui-chat__item"], ' +
                                        'div[class*="ui-chat__message"], ' +
                                        '[role="listitem"]'
                                    );

                                    if (cards && cards.length > 0) {
                                        for (const card of cards) {
                                            const authorEl = card.querySelector('.ui-chat__message__author, [class*="author"], [data-tid*="author"], [class*="speaker"], [data-tid*="speaker"], span[dir="auto"], strong, h4, h5');
                                            let speaker = cleanSpeaker(authorEl ? authorEl.innerText : '');

                                            const textEl = card.querySelector('[data-tid="closed-caption-text"], [class*="caption-text"], [class*="message-text"]');
                                            let text = '';
                                            if (textEl && textEl !== authorEl) {
                                                text = cleanText(textEl.innerText);
                                            } else {
                                                let full = cleanText(card.innerText);
                                                if (speaker && full.startsWith(speaker)) {
                                                    full = full.substring(speaker.length).trim();
                                                }
                                                text = cleanText(full);
                                            }

                                            if (text && text.length >= 2) {
                                                if (!speaker) {
                                                    for (const name of (known || [])) {
                                                        if (text.toLowerCase().startsWith(name.toLowerCase())) {
                                                            speaker = name;
                                                            text = cleanText(text.substring(name.length).replace(/^[:\\s\\-]+/, ''));
                                                            break;
                                                        }
                                                    }
                                                }
                                                if (!speaker && window.__lastKnownHumanSpeaker) {
                                                    speaker = window.__lastKnownHumanSpeaker;
                                                }
                                                if (!speaker) {
                                                    speaker = (known && known.length > 0) ? known[0] : 'Participant';
                                                }
                                                window.__lastKnownHumanSpeaker = speaker;
                                                results.push({ speaker: speaker, text: text });
                                            }
                                        }
                                    }
                                }

                                // 3. Robust line-by-line speaker state machine fallback on container innerText
                                if (results.length === 0) {
                                    for (const cont of containers) {
                                        const raw = (cont.innerText || '').trim();
                                        if (!raw || raw.length < 2) continue;

                                        const rawLines = raw.split('\\n')
                                            .map(l => l.trim())
                                            .filter(l => l && !/Turn off|caption settings|Dismiss/i.test(l));

                                        let curSpeaker = null;
                                        let curTextLines = [];

                                        function flushTurn() {
                                            if (curSpeaker && curTextLines.length > 0) {
                                                const joinedText = cleanText(curTextLines.join(' '));
                                                if (joinedText && joinedText.length >= 2) {
                                                    results.push({ speaker: curSpeaker, text: joinedText });
                                                }
                                            }
                                        }

                                        for (const line of rawLines) {
                                            if (/^[A-Z0-9]{1,3}$/.test(line)) continue;

                                            const matchedSpeaker = cleanSpeaker(line);
                                            const isKnownSpeaker = Boolean(matchedSpeaker && (
                                                (known || []).some(k => k.toLowerCase().includes(matchedSpeaker.toLowerCase()) || matchedSpeaker.toLowerCase().includes(k.toLowerCase())) ||
                                                /^[A-Z][a-z]+(\\s+[A-Z][a-z]+)+$/.test(matchedSpeaker)
                                            ));

                                            if (isKnownSpeaker) {
                                                flushTurn();
                                                curSpeaker = matchedSpeaker;
                                                window.__lastKnownHumanSpeaker = curSpeaker;
                                                curTextLines = [];
                                            } else {
                                                let handled = false;
                                                for (const name of (known || [])) {
                                                    if (line.toLowerCase().startsWith(name.toLowerCase() + ':') ||
                                                        line.toLowerCase().startsWith(name.toLowerCase() + ' ')) {
                                                        flushTurn();
                                                        curSpeaker = name;
                                                        window.__lastKnownHumanSpeaker = curSpeaker;
                                                        const rem = line.substring(name.length).replace(/^[:\\s\\-]+/, '').trim();
                                                        curTextLines = rem ? [rem] : [];
                                                        handled = true;
                                                        break;
                                                    }
                                                }
                                                if (!handled) {
                                                    if (!curSpeaker) {
                                                        curSpeaker = window.__lastKnownHumanSpeaker || 
                                                            ((known && known.length > 0) ? known[0] : 'Participant');
                                                    }
                                                    curTextLines.push(line);
                                                }
                                            }
                                        }
                                        flushTurn();
                                        if (results.length > 0) break;
                                    }
                                }

                                return results;
                            }
                        """, clean_known_list)

                        if raw_captions:
                            last_caption_received_ts = time.time()
                            captions_enabled = True
                            now_sec = round(time.time() - start_ts, 1)

                            for cap in raw_captions:
                                speaker = (cap.get("speaker") or "Participant").strip()
                                speech_txt = (cap.get("text") or "").strip()
                                if not speech_txt or len(speech_txt) < 2:
                                    continue

                                # Clean speaker name
                                clean_spk = re.sub(r'\s*\(?(?:Organiser|Organizer|Presenter|Attendee|Guest|External|You|Me|IN|TPR)\)?', '', speaker, flags=re.I).strip()
                                clean_spk = re.sub(r'\b(?:TPR|IN)\b', '', clean_spk, flags=re.I).strip()
                                for att_n in (known_attendees or []):
                                    clean_att = re.sub(r'\s*\(?(?:Organiser|Organizer|Presenter|Attendee|Guest|External|You|Me|IN|TPR)\)?', '', att_n, flags=re.I).strip()
                                    clean_att = re.sub(r'\b(?:TPR|IN)\b', '', clean_att, flags=re.I).strip()
                                    if clean_att.lower() == clean_spk.lower() or clean_spk.lower() in clean_att.lower() or clean_att.lower() in clean_spk.lower():
                                        clean_spk = clean_att
                                        break
                                speaker = clean_spk or "Participant"

                                norm_speech = re.sub(r'[^\w\s]', '', speech_txt).strip().lower()

                                # If already finalized earlier, skip (lingering old DOM card)
                                if norm_speech in completed_utterance_hashes or any(norm_speech in h for h in completed_utterance_hashes if len(norm_speech) >= 20):
                                    continue

                                # Register speaker in roster so attendance is marked present
                                if speaker not in seen_names and speaker.lower() not in SYSTEM_UI_WORDS and speaker.lower() != bot_name.lower():
                                    seen_names.add(speaker)
                                    roster_participants.append({
                                        "name": speaker,
                                        "join_time": datetime.now(timezone.utc).isoformat()
                                    })
                                    logger.info(f"[Teams Bot] Verified active speaker for attendance: {speaker}")

                                # Case A: Same speaker continuing/expanding current utterance
                                if current_utterance["text"] and current_utterance["speaker"] == speaker and (
                                    speech_txt.lower().startswith(current_utterance["text"].lower()) or
                                    current_utterance["text"].lower() in speech_txt.lower()
                                ):
                                    current_utterance["text"] = speech_txt
                                    if current_utterance["db_id"]:
                                        try:
                                            execute_write(
                                                "UPDATE meeting_transcripts SET text = %s, end_seconds = %s WHERE id = %s",
                                                (speech_txt, now_sec + 3.0, current_utterance["db_id"])
                                            )
                                        except Exception:
                                            pass
                                    if speech_segments and speech_segments[-1]["speaker_name"] == speaker:
                                        speech_segments[-1]["text"] = speech_txt
                                        speech_segments[-1]["end_seconds"] = now_sec + 3.0
                                    print(f"[LIVE CAPTIONS] #{meeting_id} [{speaker}]: \"{speech_txt}\"", flush=True)
                                    continue

                                # Case B: Identical text already recorded
                                if current_utterance["speaker"] == speaker and norm_speech == re.sub(r'[^\w\s]', '', current_utterance["text"]).strip().lower():
                                    continue

                                # Case C: Shorter prefix of already captured text (DOM flicker)
                                if current_utterance["speaker"] == speaker and current_utterance["text"].lower().startswith(speech_txt.lower()):
                                    continue

                                # Case D: New turn (speaker change or new distinct sentence)
                                if current_utterance["text"]:
                                    prev_norm = re.sub(r'[^\w\s]', '', current_utterance["text"]).strip().lower()
                                    completed_utterance_hashes.add(prev_norm)
                                    logger.info(f"[Live Captions Turn Completed] #{meeting_id} [{current_utterance['speaker']}]: \"{current_utterance['text']}\"")

                                # Check again if new speech was already completed in history
                                if norm_speech in completed_utterance_hashes or any(norm_speech in h for h in completed_utterance_hashes if len(norm_speech) >= 20):
                                    continue

                                # Insert new utterance turn into DB
                                try:
                                    new_id = execute_write(
                                        """INSERT INTO meeting_transcripts (meeting_id, speaker_name, text, start_seconds, end_seconds)
                                           VALUES (%s, %s, %s, %s, %s)""",
                                        (meeting_id, speaker, speech_txt, now_sec, now_sec + 3.0)
                                    )
                                except Exception as db_err:
                                    new_id = None
                                    logger.debug(f"Live transcript insert error: {db_err}")

                                current_utterance["speaker"] = speaker
                                current_utterance["text"] = speech_txt
                                current_utterance["db_id"] = new_id
                                current_utterance["start_sec"] = now_sec

                                speech_segments.append({
                                    "speaker_name": speaker,
                                    "text": speech_txt,
                                    "start_seconds": now_sec,
                                    "end_seconds": now_sec + 3.0
                                })
                                print(f"[LIVE CAPTIONS] #{meeting_id} [{speaker}]: \"{speech_txt}\"", flush=True)

                    except Exception as cap_err:
                        logger.warning(f"[Teams Bot] Caption scrape error: {cap_err}")

            except Exception as loop_err:
                logger.debug(f"[Teams Bot] Telemetry loop error: {loop_err}")

        # Finalize any pending utterance turn
        if current_utterance["text"]:
            prev_norm = re.sub(r'[^\w\s]', '', current_utterance["text"]).strip().lower()
            completed_utterance_hashes.add(prev_norm)
            print(f"[LIVE CAPTIONS COMPLETED] #{meeting_id} [{current_utterance['speaker']}]: \"{current_utterance['text']}\"", flush=True)
            logger.info(f"[Live Captions Turn Completed] #{meeting_id} [{current_utterance['speaker']}]: \"{current_utterance['text']}\"")

        if in_call_start_ts > 0:
            call_duration = max(1.0, time.time() - in_call_start_ts)
        else:
            call_duration = max(1.0, time.time() - start_ts)

        if (in_call or has_ever_joined_call) and (completed_normally or call_duration >= 30 or len(speech_segments) > 0 or len(roster_participants) > 0):
            completed_normally = True

        return roster_participants, speech_segments, completed_normally, call_duration

    @classmethod
    def _run_google_meet_session(
        cls,
        page,
        meeting_id: int,
        stop_event: threading.Event,
        roster_participants: Optional[List[Dict[str, Any]]] = None,
        speech_segments: Optional[List[Dict[str, Any]]] = None
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], bool, float]:
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
                return roster_participants, speech_segments, False, 0.0
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
            time.sleep(1 if in_call else 2)
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
                                    last_caption_received_ts = time.time()
                                    captions_enabled = True

                                    try:
                                        execute_write(
                                            """INSERT INTO meeting_transcripts (meeting_id, speaker_name, text, start_seconds, end_seconds)
                                               VALUES (%s, %s, %s, %s, %s)""",
                                            (meeting_id, speaker, cap_text, now_sec, now_sec + 3.0)
                                        )
                                    except Exception as db_err:
                                        logger.debug(f"Live transcript insert error: {db_err}")

                                    logger.info(f"[Live Speech] #{meeting_id} {speaker}: \"{cap_text}\"")
                                    print(f"[Live Speech] #{meeting_id} {speaker}: \"{cap_text}\"", flush=True)
                            except Exception:
                                pass

            except Exception as meet_err:
                logger.debug(f"[Google Meet Bot] Monitoring note: {meet_err}")

        call_duration = max(1.0, time.time() - start_ts)
        if in_call and not stop_event.is_set():
            completed_normally = True

        return roster_participants, speech_segments, completed_normally, call_duration
