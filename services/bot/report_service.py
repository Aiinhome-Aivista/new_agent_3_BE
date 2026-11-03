import io
import os
import logging
from datetime import datetime
from typing import Optional, Dict, Any, List

from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether, HRFlowable
)
from reportlab.pdfgen import canvas

from db import execute_query, execute_write
from services.bot.vtt_helper import format_seconds_to_time

logger = logging.getLogger("ai_meeting.report_service")

class NumberedCanvas(canvas.Canvas):
    """Adds page numbers and enterprise consent header/footer to all pages."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_decorations(num_pages)
            super().showPage()
        super().save()

    def draw_page_decorations(self, page_count):
        self.saveState()
        self.setFont("Helvetica", 8)
        self.setFillColor(colors.HexColor("#64748b"))

        # Running header
        self.drawString(54, 750, "PwC AI Meeting Intelligence Report — Enterprise Knowledge Transfer")
        self.setStrokeColor(colors.HexColor("#e2e8f0"))
        self.setLineWidth(0.5)
        self.line(54, 744, 558, 744)

        # Running footer
        page_str = f"Page {self._pageNumber} of {page_count}"
        self.drawRightString(558, 36, page_str)
        self.drawString(
            54, 36,
            "Confidential — Automated session intelligence recorded for Knowledge Transfer evaluation."
        )
        self.line(54, 46, 558, 46)
        self.restoreState()

class ReportService:
    @classmethod
    def generate_pdf(cls, meeting_id: int) -> str:
        """
        Generates a publication-grade PDF report for the meeting intelligence session
        and saves it to reports_output/meeting_{meeting_id}_intelligence.pdf.
        Returns the absolute file path.
        """
        # 1. Fetch meeting info
        meeting_res = execute_query(
            """SELECT m.*, p.application_name 
               FROM meetings m 
               LEFT JOIN kt_plans p ON m.plan_id = p.id 
               WHERE m.id = %s""",
            (meeting_id,)
        )
        if not meeting_res:
            raise ValueError(f"Meeting with ID {meeting_id} not found.")
        meeting = meeting_res[0]

        # 2. Fetch intelligence, QA, action items, decisions, attendance
        intel_res = execute_query("SELECT * FROM meeting_intelligence WHERE meeting_id = %s ORDER BY id DESC LIMIT 1", (meeting_id,))
        analysis = intel_res[0] if intel_res else {}

        questions = execute_query("SELECT * FROM meeting_qa WHERE meeting_id = %s ORDER BY id ASC", (meeting_id,))
        action_items = execute_query("SELECT * FROM meeting_action_items WHERE meeting_id = %s ORDER BY id ASC", (meeting_id,))
        decisions = execute_query("SELECT * FROM meeting_decisions WHERE meeting_id = %s ORDER BY id ASC", (meeting_id,))
        
        attendance_records = execute_query(
            """SELECT a.attended, a.notes, s.name, s.email, s.role 
               FROM attendance a 
               JOIN stakeholders s ON a.stakeholder_id = s.id 
               WHERE a.meeting_id = %s ORDER BY s.name ASC""",
            (meeting_id,)
        )

        # 3. Create document
        output_dir = os.path.abspath("reports_output")
        os.makedirs(output_dir, exist_ok=True)
        file_path = os.path.join(output_dir, f"meeting_{meeting_id}_intelligence.pdf")

        doc = SimpleDocTemplate(
            file_path,
            pagesize=letter,
            leftMargin=54,
            rightMargin=54,
            topMargin=64,
            bottomMargin=54
        )

        styles = getSampleStyleSheet()
        title_style = ParagraphStyle(
            "DocTitle",
            parent=styles["Heading1"],
            fontName="Helvetica-Bold",
            fontSize=20,
            leading=24,
            textColor=colors.HexColor("#0f172a"),
            spaceAfter=4
        )
        subtitle_style = ParagraphStyle(
            "DocSubTitle",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=9.5,
            leading=13,
            textColor=colors.HexColor("#475569"),
            spaceAfter=12
        )
        h2_style = ParagraphStyle(
            "H2Style",
            parent=styles["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=12,
            leading=16,
            textColor=colors.HexColor("#1e293b"),
            spaceBefore=10,
            spaceAfter=5
        )
        body_style = ParagraphStyle(
            "Body",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=8.5,
            leading=12,
            textColor=colors.HexColor("#334155")
        )
        summary_style = ParagraphStyle(
            "SummaryText",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=9,
            leading=13.5,
            textColor=colors.HexColor("#1e293b")
        )
        cell_header_style = ParagraphStyle(
            "CellHeader",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=8,
            leading=10.5,
            textColor=colors.HexColor("#ffffff")
        )

        elements = []

        # Title & Meta Header
        elements.append(Paragraph(meeting.get('title') or f"Meeting #{meeting_id}", title_style))
        sched_dt = meeting.get('scheduled_at')
        date_str = sched_dt.strftime("%b %d, %Y %I:%M %p") if hasattr(sched_dt, 'strftime') else str(sched_dt or "N/A")
        plan_name = meeting.get('application_name') or "KT Plan"
        
        elements.append(Paragraph(
            f"<b>Plan:</b> {plan_name} &nbsp;|&nbsp; "
            f"<b>Status:</b> {meeting.get('status', 'completed').capitalize()} &nbsp;|&nbsp; "
            f"<b>Date:</b> {date_str}",
            subtitle_style
        ))
        elements.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#cbd5e1"), spaceAfter=12))

        # KPI Metrics Table
        total_talk = analysis.get("total_duration_seconds", 0) or 0
        total_words = analysis.get("total_words", 0) or 0
        total_speakers = analysis.get("total_speakers", 0) or len(attendance_records)

        kpi_data = [
            [
                Paragraph("<b>Total Attendees</b>", body_style),
                Paragraph("<b>Session Duration</b>", body_style),
                Paragraph("<b>Total Words</b>", body_style),
                Paragraph("<b>Action Items</b>", body_style),
                Paragraph("<b>Decisions</b>", body_style),
            ],
            [
                Paragraph(f"<font size=13><b>{len(attendance_records)}</b></font>", body_style),
                Paragraph(f"<font size=13><b>{format_seconds_to_time(total_talk)}</b></font>", body_style),
                Paragraph(f"<font size=13><b>{total_words}</b></font>", body_style),
                Paragraph(f"<font size=13><b>{len(action_items)}</b></font>", body_style),
                Paragraph(f"<font size=13><b>{len(decisions)}</b></font>", body_style),
            ]
        ]
        kpi_table = Table(kpi_data, colWidths=[100, 100, 100, 100, 104])
        kpi_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f8fafc")),
            ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#e2e8f0")),
            ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ]))
        elements.append(kpi_table)
        elements.append(Spacer(1, 12))

        # Executive Summary
        exec_summary = analysis.get("executive_summary") or "Meeting completed successfully. Discussion was focused on core knowledge transfer objectives."
        elements.append(Paragraph("Executive Summary", h2_style))
        summary_table = Table(
            [[Paragraph(exec_summary.replace("\n", "<br/><br/>"), summary_style)]],
            colWidths=[504]
        )
        summary_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f1f5f9")),
            ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#cbd5e1")),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ("LEFTPADDING", (0, 0), (-1, -1), 10),
            ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ]))
        elements.append(summary_table)
        elements.append(Spacer(1, 12))

        # Attendance & Engagement Breakdown
        if attendance_records:
            elements.append(Paragraph("Participant Attendance & Engagement", h2_style))
            part_headers = ["Participant", "Email", "Role", "Attendance", "Notes"]
            part_rows = [[Paragraph(f"<b>{h}</b>", cell_header_style) for h in part_headers]]

            for p in attendance_records:
                status_str = "<font color='#16a34a'><b>Present</b></font>" if p.get('attended') else "<font color='#dc2626'>Absent</font>"
                notes_str = p.get('notes') or "Attended"
                part_rows.append([
                    Paragraph(f"<b>{p.get('name', 'N/A')}</b>", body_style),
                    Paragraph(p.get('email', ''), body_style),
                    Paragraph(p.get('role', ''), body_style),
                    Paragraph(status_str, body_style),
                    Paragraph(notes_str, body_style)
                ])

            part_table = Table(part_rows, colWidths=[120, 140, 110, 64, 70])
            part_table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1e293b")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]))
            elements.append(part_table)
            elements.append(Spacer(1, 12))

        # Action Items
        if action_items:
            elements.append(Paragraph("Action Items & Deliverables", h2_style))
            ai_headers = ["Action / Task", "Owner", "Deadline"]
            ai_rows = [[Paragraph(f"<b>{h}</b>", cell_header_style) for h in ai_headers]]
            for ai in action_items:
                owner_str = ai.get('owner') if ai.get('owner') else "<font color='#b91c1c'>Unassigned</font>"
                deadline_str = ai.get('deadline') if ai.get('deadline') else "<font color='#d97706'>Not specified</font>"
                ai_rows.append([
                    Paragraph(ai.get('task', ''), body_style),
                    Paragraph(owner_str, body_style),
                    Paragraph(deadline_str, body_style)
                ])

            ai_table = Table(ai_rows, colWidths=[284, 110, 110])
            ai_table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2563eb")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]))
            elements.append(ai_table)
            elements.append(Spacer(1, 12))

        # Key Decisions
        if decisions:
            elements.append(Paragraph("Agreed Decisions", h2_style))
            dec_rows = []
            for idx, d in enumerate(decisions, 1):
                dec_rows.append([
                    Paragraph(f"<b>{idx}.</b>", body_style),
                    Paragraph(d.get('decision_text', ''), body_style)
                ])
            dec_table = Table(dec_rows, colWidths=[24, 480])
            dec_table.setStyle(TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]))
            elements.append(dec_table)
            elements.append(Spacer(1, 12))

        # Questions & Answers
        if questions:
            elements.append(Paragraph("Questions & Technical Discussions", h2_style))
            q_headers = ["Question", "Asked By", "Status", "Answer Details"]
            q_rows = [[Paragraph(f"<b>{h}</b>", cell_header_style) for h in q_headers]]
            for q in questions:
                status_str = "<font color='#16a34a'>Answered</font>" if q.get('is_answered') else "<font color='#dc2626'><b>Unanswered</b></font>"
                ans = q.get('answer') or ""
                ans_str = f"<b>{q.get('answered_by') or 'Team'}:</b> {ans}" if ans else "<i>No direct answer recorded</i>"
                q_rows.append([
                    Paragraph(q.get('question', ''), body_style),
                    Paragraph(q.get('asked_by', 'Participant'), body_style),
                    Paragraph(status_str, body_style),
                    Paragraph(ans_str, body_style)
                ])
            q_table = Table(q_rows, colWidths=[180, 84, 80, 160])
            q_table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0f766e")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]))
            elements.append(q_table)
            elements.append(Spacer(1, 12))

        # Full Meeting Conversation Transcript
        transcripts = execute_query(
            "SELECT speaker_name, text, start_seconds, end_seconds FROM meeting_transcripts WHERE meeting_id = %s ORDER BY start_seconds ASC, id ASC",
            (meeting_id,)
        )
        elements.append(Paragraph("Full Meeting Conversation Transcript", h2_style))
        if transcripts:
            tr_headers = ["Time", "Speaker", "Transcript Dialogue"]
            tr_rows = [[Paragraph(f"<b>{h}</b>", cell_header_style) for h in tr_headers]]
            for tr in transcripts:
                sec = tr.get("start_seconds") or 0.0
                time_str = format_seconds_to_time(sec)
                spk = tr.get("speaker_name") or "Participant"
                text_content = tr.get("text") or ""
                tr_rows.append([
                    Paragraph(f"<font color='#64748b'><b>[{time_str}]</b></font>", body_style),
                    Paragraph(f"<b>{spk}</b>", body_style),
                    Paragraph(text_content, body_style)
                ])
            tr_table = Table(tr_rows, colWidths=[60, 110, 334])
            tr_table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#334155")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]))
            elements.append(tr_table)
            elements.append(Spacer(1, 12))
        else:
            no_trans_p = Paragraph(
                "<i>Verbatim audio transcript segments were not captured by meeting provider (live captions were inactive or unavailable during the call). Executive summary, deliverables, and decisions were synthesized from session context.</i>",
                body_style
            )
            elements.append(no_trans_p)
            elements.append(Spacer(1, 12))

        doc.build(elements, canvasmaker=NumberedCanvas)
        logger.info(f"Generated PDF intelligence report at {file_path}")

        # Record in meeting_reports table
        try:
            execute_write(
                "INSERT INTO meeting_reports (meeting_id, file_path) VALUES (%s, %s)",
                (meeting_id, file_path)
            )
        except Exception as rpt_err:
            logger.warning(f"Failed to record in meeting_reports: {rpt_err}")

        return file_path

