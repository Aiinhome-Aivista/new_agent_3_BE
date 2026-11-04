import json
import logging
import re
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

from db import execute_query, execute_write
from llm_service import call_llm
from services.bot.vtt_helper import format_seconds_to_time
from services.bot.report_service import ReportService

logger = logging.getLogger("ai_meeting.analysis_service")

class AnalysisEngine:
    @classmethod
    def run_post_meeting_pipeline(
        cls,
        meeting_id: int,
        roster_participants: Optional[List[Dict[str, Any]]] = None,
        speech_segments: Optional[List[Dict[str, Any]]] = None
    ) -> Dict[str, Any]:
        """
        Executes the end-to-end post-meeting analysis pipeline:
        1. Persists transcript speech segments to `meeting_transcripts`.
        2. Computes speaking metrics and identifies dominant vs silent speakers.
        3. Auto-marks attendance in `attendance` table (attended = 1).
        4. Calls LLM to extract Executive Summary, Q&A, Action Items, Decisions, and Risks.
        5. Persists structured data to `meeting_intelligence`, `meeting_qa`, `meeting_action_items`, `meeting_decisions`.
        6. Logs any detected risks into `risks` table.
        7. Transitions meeting status to 'completed' in `meetings`.
        8. Generates the executive PDF intelligence report via ReportService.
        """
        logger.info(f"Starting post-meeting pipeline for Meeting #{meeting_id}...")

        # 1. Fetch meeting info
        m_rows = execute_query(
            "SELECT m.*, p.application_name FROM meetings m LEFT JOIN kt_plans p ON m.plan_id = p.id WHERE m.id = %s",
            (meeting_id,)
        )
        if not m_rows:
            logger.error(f"Meeting #{meeting_id} not found in database.")
            return {"status": "error", "message": "Meeting not found"}
        meeting = m_rows[0]
        plan_id = meeting.get("plan_id")
        meeting_title = meeting.get("title", f"Meeting #{meeting_id}")

        # 2. Persist transcript segments if provided and not already stored
        if speech_segments:
            existing_cnt = execute_query("SELECT COUNT(*) as cnt FROM meeting_transcripts WHERE meeting_id = %s", (meeting_id,))
            has_db_segments = existing_cnt and existing_cnt[0]["cnt"] > 0
            if not has_db_segments:
                for seg in speech_segments:
                    try:
                        spk = seg.get("speaker_name") or seg.get("speaker") or "Participant"
                        txt = seg.get("text", "").strip()
                        st = float(seg.get("start_seconds", 0.0))
                        et = float(seg.get("end_seconds", st + 5.0))
                        if txt:
                            execute_write(
                                """INSERT INTO meeting_transcripts (meeting_id, speaker_name, text, start_seconds, end_seconds)
                                   VALUES (%s, %s, %s, %s, %s)""",
                                (meeting_id, spk, txt, st, et)
                            )
                    except Exception as t_err:
                        logger.debug(f"Transcript segment insert note: {t_err}")

        # Fetch all transcripts for this meeting from DB
        db_segments = execute_query(
            "SELECT * FROM meeting_transcripts WHERE meeting_id = %s ORDER BY start_seconds ASC",
            (meeting_id,)
        )

        # 3. Compute speaking statistics
        speaker_stats: Dict[str, Dict[str, Any]] = {}
        total_talk_time = 0.0
        total_words = 0

        for seg in db_segments:
            speaker = (seg.get("speaker_name") or "Participant").strip()
            text = (seg.get("text") or "").strip()
            st = float(seg.get("start_seconds") or 0.0)
            et = float(seg.get("end_seconds") or st + 3.0)
            duration = max(1.0, et - st)
            words = len(text.split())

            if speaker not in speaker_stats:
                speaker_stats[speaker] = {
                    "speaker": speaker,
                    "talk_time_seconds": 0.0,
                    "word_count": 0,
                    "segment_count": 0,
                }
            speaker_stats[speaker]["talk_time_seconds"] += duration
            speaker_stats[speaker]["word_count"] += words
            speaker_stats[speaker]["segment_count"] += 1
            total_talk_time += duration
            total_words += words

        # Determine dominant and silent participants
        dominant_speaker = None
        for spk, stats in speaker_stats.items():
            pct = (stats["talk_time_seconds"] / total_talk_time * 100.0) if total_talk_time > 0 else 0
            stats["talk_time_percentage"] = round(pct, 1)
            stats["talk_time_formatted"] = format_seconds_to_time(stats["talk_time_seconds"])
            if pct > 50.0 and spk.lower() not in ("unknown", "facilitator", "bot", "ai meeting facilitator"):
                dominant_speaker = spk

        # 4. Auto-mark attendance in `attendance` table
        attendees = execute_query(
            """SELECT a.id as attendance_id, a.stakeholder_id, s.name, s.email, s.role 
               FROM attendance a 
               JOIN stakeholders s ON a.stakeholder_id = s.id 
               WHERE a.meeting_id = %s""",
            (meeting_id,)
        )

        detected_names = set()
        if roster_participants:
            for rp in roster_participants:
                r_name = (rp.get("name") or "").strip().lower()
                if r_name and "bot" not in r_name and "facilitator" not in r_name:
                    detected_names.add(r_name)
        for spk in speaker_stats.keys():
            if spk.lower() not in ("bot", "ai meeting facilitator", "unknown"):
                detected_names.add(spk.lower())

        marked_count = 0
        for att in attendees:
            sh_name = (att.get("name") or "").strip().lower()
            matched = False

            # Exact or partial match
            if sh_name in detected_names:
                matched = True
            else:
                first_name = sh_name.split()[0] if sh_name else ""
                last_name = sh_name.split()[-1] if len(sh_name.split()) > 1 else ""
                for dn in detected_names:
                    if len(sh_name) > 3 and (sh_name in dn or dn in sh_name):
                        matched = True
                        break
                    if len(first_name) >= 3 and first_name in dn:
                        matched = True
                        break
                    if len(last_name) >= 4 and last_name in dn:
                        matched = True
                        break

            # Only mark present if matched in roster, stage tiles, or live speech
            if matched:
                spk_data = speaker_stats.get(att.get("name"), {})
                tt_str = spk_data.get("talk_time_formatted", "Joined Call")
                note = f"Verified by AI Meeting Bot ({tt_str})"
                try:
                    execute_write(
                        "UPDATE attendance SET attended = 1, notes = %s WHERE id = %s",
                        (note, att["attendance_id"])
                    )
                    marked_count += 1
                except Exception as att_upd_err:
                    logger.warning(f"Error updating attendance #{att['attendance_id']}: {att_upd_err}")
            else:
                try:
                    execute_write(
                        "UPDATE attendance SET attended = 0, notes = %s WHERE id = %s",
                        ("Absent - Not detected in call roster", att["attendance_id"])
                    )
                except Exception as att_abs_err:
                    logger.warning(f"Error marking absent #{att['attendance_id']}: {att_abs_err}")

        logger.info(f"Attendance marked for Meeting #{meeting_id}: {marked_count}/{len(attendees)} attendees verified present.")

        # 5. Extract LLM Intelligence (Summary, Q&A, Action Items, Decisions, Concerns)
        transcript_lines = []
        for s in db_segments:
            transcript_lines.append(f"{s.get('speaker_name', 'Participant')}: {s.get('text', '')}")
        full_transcript = "\n".join(transcript_lines)

        llm_data = cls._run_llm_analysis(meeting_title, full_transcript, plan_name=meeting.get("application_name", ""))

        # 6. Persist structured intelligence
        try:
            # Clear previous runs if any for idempotency
            execute_write("DELETE FROM meeting_qa WHERE meeting_id = %s", (meeting_id,))
            execute_write("DELETE FROM meeting_action_items WHERE meeting_id = %s", (meeting_id,))
            execute_write("DELETE FROM meeting_decisions WHERE meeting_id = %s", (meeting_id,))
            execute_write("DELETE FROM meeting_intelligence WHERE meeting_id = %s", (meeting_id,))

            # Insert Questions
            for q in llm_data.get("questions", []):
                execute_write(
                    """INSERT INTO meeting_qa (meeting_id, asked_by, question, answered_by, answer, is_answered)
                       VALUES (%s, %s, %s, %s, %s, %s)""",
                    (
                        meeting_id,
                        q.get("asked_by") or "Participant",
                        q.get("question") or "",
                        q.get("answered_by"),
                        q.get("answer"),
                        1 if q.get("is_answered", True) else 0
                    )
                )

            # Insert Action Items
            for a in llm_data.get("action_items", []):
                execute_write(
                    """INSERT INTO meeting_action_items (meeting_id, task, owner, deadline)
                       VALUES (%s, %s, %s, %s)""",
                    (meeting_id, a.get("task", ""), a.get("owner"), a.get("deadline"))
                )

            # Insert Decisions
            for d in llm_data.get("decisions", []):
                if isinstance(d, str) and d.strip():
                    execute_write(
                        "INSERT INTO meeting_decisions (meeting_id, decision_text) VALUES (%s, %s)",
                        (meeting_id, d.strip())
                    )

            # Insert Meeting Intelligence Record
            exec_summary = llm_data.get("executive_summary") or f"Knowledge Transfer session concluded covering planned topics for {meeting_title}."
            execute_write(
                """INSERT INTO meeting_intelligence 
                   (meeting_id, executive_summary, total_speakers, total_words, total_duration_seconds, dominant_speaker, silent_count, raw_analysis)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    meeting_id,
                    exec_summary,
                    len(speaker_stats),
                    total_words,
                    int(round(total_talk_time)),
                    dominant_speaker,
                    max(0, len(attendees) - marked_count),
                    json.dumps(llm_data)
                )
            )

            # 7. Check for critical KT Concerns / Risks and automatically record in `risks` table
            logged_risk_descs = set()

            def _log_risk(desc: str, severity: str = "medium"):
                clean_desc = desc.strip()
                if not plan_id or not clean_desc or clean_desc in logged_risk_descs:
                    return
                logged_risk_descs.add(clean_desc)
                sev = severity.lower()
                if sev not in ("low", "medium", "high", "critical"):
                    sev = "medium"
                try:
                    execute_write(
                        """INSERT INTO risks (plan_id, description, severity, status, detected_by)
                           VALUES (%s, %s, %s, 'open', 'ai')""",
                        (plan_id, f"[{meeting_title}] {clean_desc}", sev)
                    )
                    logger.info(f"Auto-logged KT session risk for plan #{plan_id}: {clean_desc[:60]}...")
                except Exception as r_err:
                    logger.warning(f"Failed to auto-log risk: {r_err}")

            # 7a. LLM-identified concerns and blockers
            for concern in llm_data.get("concerns", []):
                c_desc = concern.get("description", "")
                c_sev = concern.get("severity", "medium")
                if c_desc:
                    _log_risk(c_desc, c_sev)

            # 7b. Unanswered technical questions (High Risk to KT success)
            for q in llm_data.get("questions", []):
                if not q.get("is_answered", True) and q.get("question"):
                    asked = q.get("asked_by") or "Knowledge Receiver"
                    _log_risk(f"Unanswered Technical Question: '{q['question']}' asked by {asked} was left unresolved during the session.", "high")

            # 7c. Missing or Silent Knowledge Receivers (Risk of knowledge gap)
            if len(detected_names) > 0:
                for att in attendees:
                    role = (att.get("role") or "").lower()
                    if "receiver" in role or "incoming" in role:
                        sh_name = att.get("name") or "Receiver"
                        if sh_name.lower() not in detected_names:
                            _log_risk(f"Receiver Absence: Knowledge Receiver '{sh_name}' was absent from this scheduled topic session.", "high")
                        else:
                            spk_stats = speaker_stats.get(sh_name, {})
                            if spk_stats.get("talk_time_seconds", 0) < 20:
                                _log_risk(f"Low Receiver Engagement: '{sh_name}' had zero or minimal speaking interaction during topic handover.", "medium")

            # 7d. Action items without owner or deadline
            for a in llm_data.get("action_items", []):
                if not a.get("owner") or not a.get("deadline"):
                    _log_risk(f"Unassigned Action Item: Deliverable '{a.get('task')}' was recorded without an assigned owner or deadline.", "low")

        except Exception as persist_err:
            logger.error(f"Error persisting intelligence data: {persist_err}", exc_info=True)

        # 8. Update Topic Completion Tracking if plan has topics
        try:
            if plan_id:
                topics = execute_query(
                    "SELECT topic_name FROM plan_topics WHERE plan_id = %s",
                    (plan_id,)
                )
                if topics:
                    for t in topics:
                        t_name = t.get("topic_name")
                        if t_name:
                            execute_write(
                                """INSERT INTO completion_tracking (plan_id, topic, completion_percent)
                                   VALUES (%s, %s, 100)
                                   ON DUPLICATE KEY UPDATE completion_percent = 100""",
                                (plan_id, t_name)
                            )
        except Exception as ct_err:
            logger.debug(f"Completion tracking update note: {ct_err}")

        # 9. Mark Meeting status as 'completed'
        try:
            execute_write("UPDATE meetings SET status = 'completed' WHERE id = %s", (meeting_id,))
            execute_write(
                "UPDATE scheduled_bot_jobs SET status = 'COMPLETED' WHERE meeting_id = %s",
                (meeting_id,)
            )
            logger.info(f"Meeting #{meeting_id} status updated to 'completed'.")
        except Exception as st_err:
            logger.error(f"Failed to update meeting status: {st_err}")

        # 10. Generate PDF Intelligence Report
        pdf_path = None
        try:
            pdf_path = ReportService.generate_pdf(meeting_id)
        except Exception as pdf_err:
            logger.error(f"PDF generation failed: {pdf_err}", exc_info=True)

        return {
            "status": "success",
            "meeting_id": meeting_id,
            "attendees_present": marked_count,
            "total_attendees": len(attendees),
            "questions_count": len(llm_data.get("questions", [])),
            "action_items_count": len(llm_data.get("action_items", [])),
            "pdf_report": pdf_path
        }

    @classmethod
    def _run_llm_analysis(cls, meeting_title: str, transcript: str, plan_name: str = "") -> Dict[str, Any]:
        """Calls Mistral LLM to extract structured summary, Q&A, action items, and decisions."""
        if not transcript or len(transcript.strip()) < 20:
            return {
                "executive_summary": f"Knowledge Transfer session for {meeting_title} ({plan_name}) was conducted and concluded.",
                "questions": [],
                "action_items": [],
                "decisions": [f"Knowledge Transfer session {meeting_title} completed."],
                "concerns": []
            }

        # Truncate transcript to ~12000 chars if exceedingly long for context limits
        safe_transcript = transcript[:12000]

        prompt = f"""You are an enterprise AI meeting intelligence facilitator analyzing the Knowledge Transfer meeting '{meeting_title}' for project '{plan_name}'.

Review the session transcript and extract:
1. An Executive Summary (concise overview of concepts transferred, discussions held, and participant engagement).
2. Questions asked (with who asked, who answered, the answer, and whether it was resolved).
3. Action items & deliverables (task, owner, deadline).
4. Agreed decisions or architectural conclusions.
5. Critical concerns or risks (e.g. key question unanswered, receiver silent, scope blocker).

TRANSCRIPT:
{safe_transcript}

INSTRUCTIONS:
Return strictly a valid JSON object matching this schema:
{{
  "executive_summary": "Comprehensive 2-4 sentence summary of the session",
  "questions": [
    {{
      "asked_by": "Name of asker",
      "question": "Question text",
      "answered_by": "Name of answerer or null",
      "answer": "Answer summary or null",
      "is_answered": true
    }}
  ],
  "action_items": [
    {{
      "task": "Task description",
      "owner": "Assignee name or null",
      "deadline": "Deadline or null"
    }}
  ],
  "decisions": [
    "Decision 1",
    "Decision 2"
  ],
  "concerns": [
    {{
      "concern_type": "UNANSWERED_QUESTION / LOW_PARTICIPATION / TIMELINE_RISK",
      "severity": "LOW / MEDIUM / HIGH",
      "description": "Specific observation or risk"
    }}
  ]
}}
Only output valid JSON. Do not include markdown code block syntax if possible."""

        raw_res = call_llm(prompt)
        parsed = cls._clean_and_parse_json(raw_res)
        if parsed:
            return parsed

        return {
            "executive_summary": f"Knowledge Transfer session '{meeting_title}' was successfully completed.",
            "questions": [],
            "action_items": [],
            "decisions": [f"Session '{meeting_title}' topics covered."],
            "concerns": []
        }

    @staticmethod
    def _clean_and_parse_json(text: str) -> Optional[Dict[str, Any]]:
        """Cleans and extracts JSON object from LLM response."""
        if not text:
            return None
        cleaned = text.strip()
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]
        elif cleaned.startswith("```"):
            cleaned = cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()

        try:
            return json.loads(cleaned)
        except Exception:
            match = re.search(r"\{.*\}", cleaned, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group(0))
                except Exception:
                    pass
        return None
