import os
import time
import urllib.parse
import threading
import logging
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

        try:
            with sync_playwright() as p:
                profile_dir = os.path.abspath("teams_bot_profile")
                os.makedirs(profile_dir, exist_ok=True)

                logger.info(f"Launching Chromium context for meeting #{meeting_id}...")
                context = p.chromium.launch_persistent_context(
                    user_data_dir=profile_dir,
                    headless=headless,
                    permissions=["microphone", "camera"],
                    args=[
                        "--use-fake-ui-for-media-stream",
                        "--use-fake-device-for-media-stream",
                        "--disable-blink-features=AutomationControlled",
                        "--no-sandbox",
                    ],
                    viewport={"width": 1280, "height": 800},
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                )
                page = context.pages[0] if context.pages else context.new_page()

                logger.info(f"[Bot] Navigating to meeting URL: {join_url[:100]}...")
                try:
                    page.goto(join_url, wait_until="domcontentloaded", timeout=60000)
                except Exception as nav_err:
                    logger.warning(f"[Bot] goto warning: {nav_err}")

                # Run session based on platform
                if "teams.microsoft.com" in join_url or "teams.live.com" in join_url:
                    captured_roster, captured_speech, completed_normally = cls._run_teams_session(page, meeting_id, stop_event)
                elif "meet.google.com" in join_url:
                    captured_roster, captured_speech, completed_normally = cls._run_google_meet_session(page, meeting_id, stop_event)
                else:
                    logger.warning(f"Unrecognized meeting URL: {join_url}, trying generic teams session...")
                    captured_roster, captured_speech, completed_normally = cls._run_teams_session(page, meeting_id, stop_event)

                try:
                    context.close()
                except Exception:
                    pass

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
    def _run_teams_session(cls, page, meeting_id: int, stop_event: threading.Event) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], bool]:
        """Automates MS Teams join, lobby waiting, participant scraping, and captions."""
        logger.info(f"[Teams Bot] Starting session for meeting #{meeting_id}...")
        bot_name = Config.BOT_NAME
        roster_participants: List[Dict[str, Any]] = []
        speech_segments: List[Dict[str, Any]] = []
        seen_captions: Set[str] = set()
        seen_names: Set[str] = set()

        in_call = False
        in_lobby = False
        has_ever_joined_call = False
        completed_normally = False
        start_ts = time.time()
        last_speech_time = time.time()
        last_human_seen_time = time.time()
        alone_since = None
        consecutive_alone_checks = 0
        join_clicked = False
        roster_panel_opened = False
        captions_enabled = False

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
                body_text = page.locator("body").inner_text(timeout=500).lower()

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
                            logger.info(f"[Teams Bot] Clicking '{sel}'...")
                            b.first.click()
                            time.sleep(3)
                            break
                    except Exception:
                        pass

                # 2. Fill Display Name if on prejoin screen
                name_input = page.locator(
                    'input[data-tid="prejoin-display-name-input"], input#username, input[name="username"], input[aria-label*="name" i], input[placeholder*="name" i]'
                )
                if name_input.count() > 0 and name_input.first.is_visible(timeout=300):
                    val = name_input.first.input_value()
                    if not val or val.strip() == "":
                        logger.info(f"[Teams Bot] Entering display name: '{bot_name}'")
                        name_input.first.fill(bot_name)
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

                # 5. Click Join Button
                if not join_clicked:
                    for join_sel in [
                        'button:has-text("Join now")',
                        'button[data-tid="prejoin-join-button"]',
                        'button:has-text("Join meeting")',
                        'button:has-text("Join")'
                    ]:
                        try:
                            jb = page.locator(join_sel)
                            if jb.count() > 0 and jb.first.is_visible(timeout=300):
                                logger.info(f"[Teams Bot] Clicking Join Button ({join_sel})...")
                                jb.first.click()
                                join_clicked = True
                                time.sleep(3)
                                break
                        except Exception:
                            pass

                # 6. Check if in lobby or already entered call
                if "let you in shortly" in body_text or "waiting in the lobby" in body_text or "let you in soon" in body_text:
                    in_lobby = True
                    logger.info(f"[Teams Bot] Waiting in lobby for meeting #{meeting_id}...")
                    break

                # If leave button or in-call controls appear right away
                for l_chk in ['button:has-text("Leave")', 'button:has-text("Hang up")', 'button[data-tid="hangup-button"]', 'button[aria-label*="Leave" i]']:
                    if page.locator(l_chk).count() > 0:
                        in_call = True
                        has_ever_joined_call = True
                        last_human_seen_time = time.time()
                        last_speech_time = time.time()
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

        while not stop_event.is_set():
            time.sleep(2)
            try:
                body_text = page.locator("body").inner_text(timeout=500).lower()

                # Check if still in lobby
                in_lobby_phrases = [
                    "let you in shortly",
                    "waiting in the lobby",
                    "let you in soon",
                    "we'll let people know you're waiting",
                    "someone in the meeting should let you in"
                ]
                is_lobby_text = any(phrase in body_text for phrase in in_lobby_phrases)

                # Check in-call controls and elements
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
                    'button[data-tid="roster-button"]',
                    'button:has-text("People")',
                    'button[aria-label*="people" i]',
                    'button[aria-label*="participants" i]',
                    'button:has-text("Chat")',
                    'button[data-tid="chat-button"]',
                    'button:has-text("More")',
                    'button[data-tid="more-actions-button"]',
                    'video'
                ]:
                    try:
                        if page.locator(c_sel).count() > 0:
                            has_controls = True
                            break
                    except Exception:
                        pass

                # If lobby message disappeared and call controls / buttons are detected (or "leave" in body_text)
                if not is_lobby_text and (has_leave_btn or has_controls or ("leave" in body_text and "join" not in body_text)):
                    if not in_call:
                        in_call = True
                        has_ever_joined_call = True
                        in_lobby = False
                        last_human_seen_time = time.time()
                        last_speech_time = time.time()
                        logger.info(f"[Teams Bot] Bot has been admitted into live call for meeting #{meeting_id}!")
                        try:
                            execute_write("UPDATE scheduled_bot_jobs SET status = 'IN_CALL' WHERE meeting_id = %s", (meeting_id,))
                        except Exception:
                            pass

                # If in call, open People panel once to reveal participants
                if in_call and not roster_panel_opened:
                    for r_sel in [
                        'button[data-tid="roster-button"]',
                        'button:has-text("People")',
                        'button[aria-label*="people" i]',
                        'button[aria-label*="show participants" i]',
                        'button[aria-label*="participants" i]'
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

                # If in call, attempt to enable live captions once
                if in_call and not captions_enabled:
                    for more_sel in ['button[data-tid="more-actions-button"]', 'button:has-text("More")', 'button[aria-label*="More" i]']:
                        try:
                            mb = page.locator(more_sel)
                            if mb.count() > 0 and mb.first.is_visible(timeout=300):
                                mb.first.click()
                                time.sleep(0.5)
                                for cap_btn_sel in ['button:has-text("Turn on live captions")', 'div:has-text("Turn on live captions")', '[aria-label*="live captions" i]']:
                                    cap_b = page.locator(cap_btn_sel)
                                    if cap_b.count() > 0 and cap_b.first.is_visible(timeout=300):
                                        cap_b.first.click()
                                        captions_enabled = True
                                        logger.info(f"[Teams Bot] Enabled live captions for meeting #{meeting_id}.")
                                        break
                                page.keyboard.press("Escape")
                                break
                        except Exception:
                            pass

                # If still in lobby, log periodic heartbeat & enforce lobby timeout
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

                # Check if host explicitly denied bot in lobby
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

                # Check if meeting concluded or bot was removed by host
                conclusion_phrases = [
                    "the meeting has ended",
                    "this meeting has ended",
                    "the call has ended",
                    "you left the meeting",
                    "you've been removed",
                    "meeting ended",
                    "the host ended the meeting",
                    "you were removed from the meeting",
                    "no longer in the meeting",
                    "rejoin",
                    "how was the call quality"
                ]
                if any(phrase in body_text for phrase in conclusion_phrases):
                    if in_call or has_ever_joined_call:
                        logger.info(f"[Teams Bot] Meeting #{meeting_id} conclusion detected via UI message.")
                        completed_normally = True
                    else:
                        logger.warning(f"[Teams Bot] Host denied or dismissed bot before call started for meeting #{meeting_id}.")
                        completed_normally = False
                        try:
                            execute_write("UPDATE scheduled_bot_jobs SET status = 'FAILED', error_message = 'Host dismissed bot before entering call' WHERE meeting_id = %s", (meeting_id,))
                        except Exception:
                            pass
                    break

                # Auto-conclude phrases ONLY when call is genuinely empty after having run
                # (Notice: NEVER include 'waiting for others to join' which is a pre-call waiting message!)
                everyone_left_phrases = [
                    "only one on the call",
                    "only one here",
                    "only person in this meeting",
                    "only person here",
                    "everyone else has left",
                    "everyone else left",
                    "nobody else is here",
                    "no one else is here",
                    "we'll end the meeting soon",
                    "because you're the only person",
                    "this meeting will end in",
                    "looks like you're alone",
                    "you're alone",
                    "all participants have left",
                    "would you like to leave",
                    "are you still there"
                ]
                is_alone_by_text = any(phrase in body_text for phrase in everyone_left_phrases)

                # Check active human count in roster (Scoping to roster panels, ignoring app navigation items)
                active_human_count = 0
                if in_call:
                    roster_selectors = [
                        'div[data-tid="roster-panel"] [role="listitem"]',
                        'aside[aria-label*="Participants" i] [role="listitem"]',
                        'div[aria-label*="Participants" i] [role="listitem"]',
                        'div[data-tid*="participant"]',
                        'div[data-tid*="roster"]',
                        'div[class*="roster-participant"]',
                        'div[class*="rosterParticipant"]'
                    ]
                    p_active = page.locator(", ".join(roster_selectors))
                    for k in range(min(p_active.count(), 50)):
                        try:
                            item_t = p_active.nth(k).inner_text(timeout=100).splitlines()[0].strip().lower()
                            if item_t and len(item_t) > 2 and item_t not in SYSTEM_UI_WORDS:
                                active_human_count += 1
                        except Exception:
                            pass

                # Check active stage participant / video stream tiles (including avatars when camera is off)
                stage_tiles_count = 0
                if in_call:
                    stage_selectors = [
                        'video',
                        'div[data-tid*="video-tile"]',
                        'div[data-tid*="participant-tile"]',
                        'div[data-tid*="calling-tile"]',
                        'div[data-cid*="calling-participant-stream"]',
                        'div[class*="participant-stream"]',
                        'div[class*="stage-item"]',
                        'div[class*="avatar"]'
                    ]
                    stage_tiles_count = page.locator(", ".join(stage_selectors)).count()

                if active_human_count > 0 or stage_tiles_count > 1 or len(seen_names) > 0:
                    last_human_seen_time = time.time()

                # Guardrail: Bot MUST be in call for at least 120 seconds (2 mins) before empty-call exit can trigger
                time_in_call = time.time() - start_ts
                is_empty_call = False

                if in_call and time_in_call >= 120:
                    # Condition 1: Explicit banner that everyone left
                    if is_alone_by_text:
                        is_empty_call = True
                    # Condition 2: Attendees were previously seen/recorded, and now all left (0 active humans for >= 30s)
                    elif (len(seen_names) > 0 or len(speech_segments) > 0) and active_human_count == 0 and stage_tiles_count <= 1:
                        if (time.time() - last_human_seen_time) >= 30:
                            is_empty_call = True

                if is_empty_call:
                    consecutive_alone_checks += 1
                    if alone_since is None:
                        alone_since = time.time()
                        logger.info(
                            f"[Teams Bot] Meeting #{meeting_id}: possible empty call detected "
                            f"(roster_humans={active_human_count}, stage_tiles={stage_tiles_count}, banner={is_alone_by_text}, call_duration={int(time_in_call)}s). Verifying..."
                        )
                    elif (time.time() - alone_since >= 25) or consecutive_alone_checks >= 10:
                        logger.info(f"[Teams Bot] All human attendees have departed meeting #{meeting_id}. Executing auto-leave.")
                        for leave_btn_sel in [
                            'button[data-tid="hangup-button"]',
                            '#hangup-button',
                            'button[data-tid="call-end"]',
                            'button[aria-label*="Leave" i]',
                            'button[aria-label*="Hang up" i]',
                            'button:has-text("Leave")',
                            'button:has-text("Hang up")'
                        ]:
                            try:
                                lb = page.locator(leave_btn_sel)
                                if lb.count() > 0 and lb.first.is_visible(timeout=300):
                                    lb.first.click()
                                    time.sleep(1)
                                    confirm_btn = page.locator('button:has-text("Leave meeting"), button:has-text("Leave call")')
                                    if confirm_btn.count() > 0 and confirm_btn.first.is_visible(timeout=300):
                                        confirm_btn.first.click()
                                    break
                            except Exception:
                                pass
                        completed_normally = True
                        break
                else:
                    alone_since = None
                    consecutive_alone_checks = 0

                # Inactivity safety guardrail:
                # If call elapsed > 3 minutes, and no speech for >60s and no humans seen:
                call_elapsed = time.time() - start_ts
                if in_call and call_elapsed > 180:
                    if (time.time() - last_speech_time > 60) and (active_human_count == 0 or stage_tiles_count <= 1):
                        logger.info(f"[Teams Bot] Meeting #{meeting_id} silence timeout (>60s idle/alone). Concluding.")
                        completed_normally = True
                        break

                # Scrape participants if in call
                if in_call:
                    roster_selectors = [
                        'div[data-tid="roster-panel"] [role="listitem"]',
                        'aside[aria-label*="Participants" i] [role="listitem"]',
                        'div[aria-label*="Participants" i] [role="listitem"]',
                        'div[data-tid="roster-participant"]',
                        'li[data-tid="roster-participant"]',
                        'div[class*="roster-participant"]',
                        'div[class*="rosterParticipant"]'
                    ]
                    p_els = page.locator(", ".join(roster_selectors))
                    if p_els.count() > 0:
                        for i in range(min(p_els.count(), 30)):
                            try:
                                raw_t = p_els.nth(i).inner_text(timeout=200)
                                name_text = raw_t.splitlines()[0].strip() if raw_t else ""
                                if name_text and len(name_text) > 2 and name_text.lower() not in SYSTEM_UI_WORDS:
                                    if name_text not in seen_names:
                                        seen_names.add(name_text)
                                        roster_participants.append({
                                            "name": name_text,
                                            "join_time": datetime.now(timezone.utc).isoformat()
                                        })
                            except Exception:
                                pass

                # Scrape live captions if in call
                if in_call:
                    for cap_sel in [
                        'div[data-tid="closed-caption"]',
                        'div[class*="closed-caption"]',
                        'div[class*="caption-text"]',
                        'div[class*="subtitle"]'
                    ]:
                        caption_els = page.locator(cap_sel)
                        if caption_els.count() > 0:
                            for j in range(caption_els.count()):
                                try:
                                    full_cap = caption_els.nth(j).inner_text(timeout=200).strip()
                                    if full_cap and full_cap not in seen_captions:
                                        seen_captions.add(full_cap)
                                        last_speech_time = time.time()
                                        parts = full_cap.split(":", 1)
                                        spk = parts[0].strip() if len(parts) > 1 else "Participant"
                                        txt = parts[1].strip() if len(parts) > 1 else full_cap
                                        now_sec = time.time() - start_ts
                                        speech_segments.append({
                                            "speaker_name": spk,
                                            "text": txt,
                                            "start_seconds": now_sec,
                                            "end_seconds": now_sec + 3.0
                                        })
                                except Exception:
                                    pass
                            break

            except Exception as loop_err:
                logger.debug(f"[Teams Bot] Telemetry loop error: {loop_err}")

        # If call lasted >60 seconds or in-call was reached, mark completed
        call_duration = time.time() - start_ts
        if (in_call or has_ever_joined_call) and (completed_normally or call_duration >= 60 or len(speech_segments) > 0 or len(roster_participants) > 0):
            completed_normally = True

        return roster_participants, speech_segments, completed_normally

    @classmethod
    def _run_google_meet_session(cls, page, meeting_id: int, stop_event: threading.Event) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], bool]:
        """Automates Google Meet join, people panel scraping, and captions."""
        logger.info(f"[Google Meet Bot] Starting session for meeting #{meeting_id}...")
        bot_name = Config.BOT_NAME
        roster_participants: List[Dict[str, Any]] = []
        speech_segments: List[Dict[str, Any]] = []
        seen_captions: Set[str] = set()
        seen_names: Set[str] = set()
        start_ts = time.time()
        in_call = False
        completed_normally = False
        gm_alone_since = None

        # Step 1: Pre-join
        time.sleep(3)
        try:
            name_input = page.locator('input[aria-label="Your name"], input[placeholder="Your name"]')
            if name_input.count() > 0:
                name_input.first.fill(bot_name)
                time.sleep(0.5)

            # Mute camera & mic
            page.keyboard.press("Control+e")
            time.sleep(0.2)
            page.keyboard.press("Control+d")
            time.sleep(0.5)

            # Click Join / Ask to join
            join_btn = page.locator('button:has-text("Ask to join"), button:has-text("Join now")')
            if join_btn.count() > 0:
                join_btn.first.click()
                time.sleep(4)
                in_call = True
                execute_write("UPDATE scheduled_bot_jobs SET status = 'IN_CALL' WHERE meeting_id = %s", (meeting_id,))

            # Turn on Captions ('c')
            page.keyboard.press("c")
            time.sleep(0.5)

            # Open people panel
            people_btn = page.locator('button[aria-label*="People" i], button[aria-label*="participants" i]')
            if people_btn.count() > 0:
                people_btn.first.click()
                time.sleep(1)

        except Exception as prejoin_err:
            logger.warning(f"[Google Meet Bot] Prejoin error: {prejoin_err}")

        # Step 2: Monitoring Loop
        while not stop_event.is_set():
            time.sleep(2)
            try:
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

                # Auto-conclude when all attendees have left Google Meet
                gm_empty_phrases = ["you're the only one here", "everyone else left", "you are the only one here", "waiting for others"]
                gm_is_empty_text = any(p in body_text for p in gm_empty_phrases)
                gm_p_items = page.locator('div[aria-label*="People" i] [role="listitem"]')
                gm_active_count = gm_p_items.count() if gm_p_items.count() > 0 else 0

                if in_call and (gm_is_empty_text or (len(seen_names) > 0 and gm_active_count <= 1)):
                    if gm_alone_since is None:
                        gm_alone_since = time.time()
                    elif time.time() - gm_alone_since >= 10:
                        logger.info(f"[Google Meet Bot] All attendees have left meeting #{meeting_id}. Auto-leaving.")
                        try:
                            page.locator('button[aria-label*="Leave call" i]').first.click()
                        except Exception:
                            pass
                        completed_normally = True
                        break
                else:
                    gm_alone_since = None

                # Scrape participants
                p_items = page.locator('div[role="listitem"]')
                for i in range(p_items.count()):
                    try:
                        p_name = p_items.nth(i).locator('span').first.inner_text().strip()
                        if p_name and p_name != bot_name and p_name not in seen_names:
                            seen_names.add(p_name)
                            roster_participants.append({
                                "name": p_name,
                                "join_time": datetime.now(timezone.utc).isoformat()
                            })
                    except Exception:
                        pass

                # Scrape captions
                cap_container = page.locator('div[class*="caption"], div[class*="Captions"]')
                if cap_container.count() > 0:
                    cap_text = cap_container.first.inner_text().strip()
                    if cap_text and cap_text not in seen_captions:
                        seen_captions.add(cap_text)
                        now_sec = time.time() - start_ts
                        speech_segments.append({
                            "speaker_name": "Participant",
                            "text": cap_text,
                            "start_seconds": now_sec,
                            "end_seconds": now_sec + 3.0
                        })

            except Exception as meet_err:
                logger.debug(f"[Google Meet Bot] Monitoring note: {meet_err}")

        if in_call and not stop_event.is_set():
            completed_normally = True

        return roster_participants, speech_segments, completed_normally
