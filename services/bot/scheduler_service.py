import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.date import DateTrigger

from config import Config
from db import execute_query, execute_write
from services.bot.browser_bot_service import BrowserMeetingBot

logger = logging.getLogger("ai_meeting.scheduler_service")

class MeetingSchedulerService:
    _scheduler: Optional[BackgroundScheduler] = None
    _is_started: bool = False

    @classmethod
    def get_scheduler(cls) -> BackgroundScheduler:
        if cls._scheduler is None:
            cls._scheduler = BackgroundScheduler(daemon=True)
        return cls._scheduler

    @classmethod
    def start_scheduler(cls):
        """Starts the background scheduler and loads pending scheduled bot meetings."""
        import os
        if getattr(Config, "FLASK_DEBUG", False) and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
            logger.debug("MeetingSchedulerService: Skipping scheduler start in Flask reloader parent process.")
            return

        if cls._is_started:
            return

        sched = cls.get_scheduler()
        if not sched.running:
            sched.start()
            cls._is_started = True
            logger.info("MeetingSchedulerService: APScheduler background engine started.")

        # Safety periodic poller every 45 seconds
        sched.add_job(
            cls.poll_due_meetings,
            trigger="interval",
            seconds=45,
            id="bot_due_meetings_poller",
            replace_existing=True
        )

        # Load future pending meetings from database
        cls._load_pending_jobs_from_db()

    @classmethod
    def schedule_meeting_job(
        cls,
        meeting_id: int,
        scheduled_at: Any,
        meeting_link: str,
        title: str = ""
    ) -> bool:
        """
        Schedules a meeting for autonomous bot joining:
        1. Persists to scheduled_bot_jobs table.
        2. Sets up a one-shot trigger in APScheduler at (scheduled_at - BOT_JOIN_LEAD_MINUTES).
        """
        try:
            # Parse datetime
            dt = cls._parse_datetime(scheduled_at)
            formatted_dt = dt.strftime("%Y-%m-%d %H:%M:%S")

            # Persist or update in scheduled_bot_jobs
            check_q = "SELECT id FROM scheduled_bot_jobs WHERE meeting_id = %s"
            existing = execute_query(check_q, (meeting_id,))
            if existing:
                upd_q = """UPDATE scheduled_bot_jobs 
                           SET scheduled_at = %s, meeting_link = %s, status = 'SCHEDULED' 
                           WHERE meeting_id = %s"""
                execute_write(upd_q, (formatted_dt, meeting_link, meeting_id))
            else:
                ins_q = """INSERT INTO scheduled_bot_jobs (meeting_id, scheduled_at, meeting_link, status) 
                           VALUES (%s, %s, %s, 'SCHEDULED')"""
                execute_write(ins_q, (meeting_id, formatted_dt, meeting_link))

            # Calculate trigger time (defaults to exact meeting start time: lead_mins = 0)
            lead_mins = getattr(Config, "BOT_JOIN_LEAD_MINUTES", 0)
            trigger_dt = dt - timedelta(minutes=lead_mins)
            now = datetime.now()

            sched = cls.get_scheduler()
            job_id = f"meeting_bot_job_{meeting_id}"

            if trigger_dt > now:
                run_time = trigger_dt
                logger.info(f"Meeting #{meeting_id} scheduled for bot join at {run_time} (Meeting start: {formatted_dt})")
            else:
                # If meeting start time is right now (within +/- 2 minutes)
                secs_diff = (now - dt).total_seconds()
                if -60 <= secs_diff <= 120:
                    run_time = now + timedelta(seconds=5)
                    logger.info(f"Meeting #{meeting_id} is starting right now; triggering bot in 5s...")
                else:
                    logger.info(f"Meeting #{meeting_id} scheduled time {formatted_dt} is in the past (>2m ago). Bot will not auto-join.")
                    return False

            sched.add_job(
                cls.trigger_bot_for_meeting,
                trigger=DateTrigger(run_date=run_time),
                args=[meeting_id],
                id=job_id,
                replace_existing=True
            )
            return True

        except Exception as e:
            logger.error(f"Failed to schedule bot job for meeting #{meeting_id}: {e}", exc_info=True)
            return False

    @classmethod
    def reschedule_meeting_job(cls, meeting_id: int, new_scheduled_at: Any) -> bool:
        """Updates date/time of a scheduled meeting job."""
        try:
            # Fetch link
            q = "SELECT meeting_link, title FROM meetings WHERE id = %s"
            rows = execute_query(q, (meeting_id,))
            link = rows[0]["meeting_link"] if rows else ""
            title = rows[0]["title"] if rows else ""

            # Cancel old job
            cls.cancel_meeting_job(meeting_id)

            # Re-schedule
            return cls.schedule_meeting_job(meeting_id, new_scheduled_at, link, title)
        except Exception as e:
            logger.error(f"Error rescheduling meeting #{meeting_id}: {e}", exc_info=True)
            return False

    @classmethod
    def cancel_meeting_job(cls, meeting_id: int):
        """Cancels a scheduled bot job and immediately stops any running browser bot instance."""
        try:
            from services.bot.browser_bot_service import BrowserMeetingBot
            BrowserMeetingBot.stop_bot(meeting_id)

            sched = cls.get_scheduler()
            job_id = f"meeting_bot_job_{meeting_id}"
            if sched.get_job(job_id):
                sched.remove_job(job_id)
            execute_write(
                "UPDATE scheduled_bot_jobs SET status = 'CANCELLED' WHERE meeting_id = %s",
                (meeting_id,)
            )
            logger.info(f"Cancelled bot job and stopped any running bot for meeting #{meeting_id}.")
        except Exception as e:
            logger.debug(f"Cancel note: {e}")

    @classmethod
    def trigger_bot_for_meeting(cls, meeting_id: int):
        """Dispatches the browser meeting bot to join the live meeting link."""
        if BrowserMeetingBot.is_active(meeting_id):
            logger.info(f"Meet Agent Bot is already actively running for meeting #{meeting_id}. Ignoring duplicate trigger.")
            return

        logger.info(f"Triggering autonomous Meet Agent Bot for meeting #{meeting_id}...")
        try:
            # Check meeting link and status
            q = "SELECT meeting_link, status, title FROM meetings WHERE id = %s"
            rows = execute_query(q, (meeting_id,))
            if not rows:
                logger.error(f"Meeting #{meeting_id} not found.")
                return

            meeting = rows[0]
            if meeting.get("status") in ("completed", "cancelled"):
                logger.info(f"Meeting #{meeting_id} is already '{meeting.get('status')}'. Bot will not join.")
                return

            # Check if job is already in progress or completed
            job_rows = execute_query("SELECT status FROM scheduled_bot_jobs WHERE meeting_id = %s", (meeting_id,))
            if job_rows and job_rows[0].get("status") in ("JOINING", "IN_CALL", "COMPLETED"):
                logger.info(f"Meeting #{meeting_id} bot job is already '{job_rows[0].get('status')}'. Skipping trigger.")
                return

            link = (meeting.get("meeting_link") or "").strip()
            if not link:
                logger.warning(f"Meeting #{meeting_id} does not have a meeting link. Skipping.")
                return

            # Mark status as JOINING before starting the worker thread
            execute_write(
                "UPDATE scheduled_bot_jobs SET status = 'JOINING', retry_count = retry_count + 1 WHERE meeting_id = %s",
                (meeting_id,)
            )

            # Launch Playwright Chromium Bot
            BrowserMeetingBot.start_bot(meeting_id, link)

        except Exception as e:
            logger.error(f"Failed to trigger bot for meeting #{meeting_id}: {e}", exc_info=True)

    @classmethod
    def poll_due_meetings(cls):
        """Fail-safe poller that checks for any meetings starting right now that were not yet triggered."""
        try:
            now = datetime.now()
            # Safety poller should only catch meetings that are currently due or missed (within the last 10 minutes)
            # Future meetings are handled by APScheduler DateTrigger with pre-join lead time.
            threshold_start = now - timedelta(minutes=10)
            threshold_end = now

            q = """
                SELECT j.meeting_id, j.meeting_link, j.scheduled_at 
                FROM scheduled_bot_jobs j
                JOIN meetings m ON j.meeting_id = m.id
                WHERE j.status = 'SCHEDULED' 
                  AND m.status = 'scheduled'
                  AND j.scheduled_at >= %s AND j.scheduled_at <= %s
            """
            due_jobs = execute_query(
                q,
                (threshold_start.strftime("%Y-%m-%d %H:%M:%S"), threshold_end.strftime("%Y-%m-%d %H:%M:%S"))
            )

            for job in due_jobs:
                m_id = job["meeting_id"]
                if BrowserMeetingBot.is_active(m_id):
                    continue
                logger.info(f"[Safety Poller] Detected due meeting #{m_id}; triggering bot...")
                cls.trigger_bot_for_meeting(m_id)

        except Exception as e:
            logger.debug(f"Safety poller note: {e}")

    @classmethod
    def _load_pending_jobs_from_db(cls):
        """Loads upcoming scheduled meetings from DB into the APScheduler queue on server start."""
        try:
            now = datetime.now()
            q = """
                SELECT j.meeting_id, j.scheduled_at, j.meeting_link, m.title 
                FROM scheduled_bot_jobs j
                JOIN meetings m ON j.meeting_id = m.id
                WHERE j.status = 'SCHEDULED' 
                  AND m.status = 'scheduled'
                  AND j.scheduled_at >= %s
            """
            upcoming = execute_query(q, (now.strftime("%Y-%m-%d %H:%M:%S"),))
            count = 0
            for job in upcoming:
                cls.schedule_meeting_job(
                    meeting_id=job["meeting_id"],
                    scheduled_at=job["scheduled_at"],
                    meeting_link=job["meeting_link"],
                    title=job.get("title", "")
                )
                count += 1
            if count > 0:
                logger.info(f"Loaded and registered {count} future scheduled bot meetings into APScheduler.")
        except Exception as e:
            logger.warning(f"Error loading pending bot jobs: {e}")

    @staticmethod
    def _parse_datetime(dt_val: Any) -> datetime:
        """Parses various datetime string or object representations."""
        if isinstance(dt_val, datetime):
            return dt_val
        if isinstance(dt_val, str):
            dt_str = dt_val.strip()
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
                try:
                    return datetime.strptime(dt_str.split(".")[0].replace("Z", ""), fmt)
                except ValueError:
                    pass
            from dateutil import parser
            return parser.parse(dt_str)
        return datetime.now()

