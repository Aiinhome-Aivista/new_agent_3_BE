import json
import logging
import re
from db import execute_write, execute_query
from llm_service import call_llm, load_prompt

def resolve_stakeholder_for_user(user_email, user_full_name, user_role):
    """Find an existing stakeholder matching this user's email, or create one."""
    existing = execute_query("SELECT id FROM stakeholders WHERE email = %s", (user_email,))
    if existing:
        return existing[0]['id']

    # Map the users.role value to the stakeholders.role ENUM
    role_map = {
        'leadership': 'leadership',
        'PwC Leadership': 'PwC Leadership',
        'engagement_manager': 'engagement_manager',
        'Delivery / Engagement Manager': 'Delivery / Engagement Manager',
        'manager': 'engagement_manager',
        'outgoing_sme': 'outgoing_sme',
        'Outgoing SME (Knowledge Giver)': 'Outgoing SME (Knowledge Giver)',
        'incoming_member': 'incoming_member',
        'Incoming Team Member (Knowledge Receiver)': 'Incoming Team Member (Knowledge Receiver)',
    }
    mapped_role = role_map.get(user_role, user_role)

    new_id = execute_write(
        "INSERT INTO stakeholders (name, email, role) VALUES (%s, %s, %s)",
        (user_full_name, user_email, mapped_role)
    )
    return new_id

def generate_plan_service(application_name, scope_description, plan_type, user_email=None, user_full_name=None, user_role=None, reverse_kt_focus=None, project_config=None, project_id=None):
    created_by = None
    if user_email and user_full_name and user_role:
        created_by = resolve_stakeholder_for_user(user_email, user_full_name, user_role)

    focus_text = f"\n    Reverse KT Focus Area: {reverse_kt_focus}" if reverse_kt_focus and plan_type == 'Reverse-KT' else ""
    project_config_text = f"\n    Project Configuration Details:\n{json.dumps(project_config, indent=2)}" if project_config else ""
    
    prompt = load_prompt(
        "plan_generation.txt",
        plan_type=plan_type,
        application_name=application_name,
        scope_description=scope_description,
        focus_text=focus_text,
        project_config_text=project_config_text
    )

    # Call LLM
    generated_content = call_llm(prompt)
    
    # Save to DB as draft
    query = """
        INSERT INTO kt_plans (application_name, scope_description, plan_type, generated_content, status, created_by, project_config, project_id)
        VALUES (%s, %s, %s, %s, 'draft', %s, %s, %s)
    """
    params = (application_name, scope_description, plan_type, generated_content, created_by, json.dumps(project_config) if project_config else None, project_id)
    plan_id = execute_write(query, params)
    
    # Extract topics
    extract_and_save_topics(plan_id, generated_content)
    
    return {
        "id": plan_id,
        "generated_content": generated_content,
        "status": "draft"
    }

def extract_and_save_topics(plan_id, generated_content):
    extraction_prompt = load_prompt("plan_topic_extraction.txt", generated_content=generated_content)
    try:
        extraction_response = call_llm(extraction_prompt)
        
        import re
        clean_json = extraction_response.strip()
        match = re.search(r'\[.*\]', clean_json, re.DOTALL)
        if match:
            clean_json = match.group(0)
        else:
            raise ValueError("No JSON array found in LLM response")
            
        topics = json.loads(clean_json)

        # Check if post-KA phases are present in extracted topics; if not, parse and append them cleanly
        has_asmt = any('assessment' in str(t.get('day_label', '') + t.get('topic_name', '')).lower() for t in topics)
        has_sr = any('shadow' in str(t.get('day_label', '') + t.get('topic_name', '')).lower() for t in topics)
        has_lr = any('lead' in str(t.get('day_label', '') + t.get('topic_name', '')).lower() for t in topics)

        if not (has_asmt and has_sr and has_lr):
            asmt_match = re.search(r'Day\s+(\d+)\s+to\s+Day\s+(\d+):[^\n]*Final\s*Assessment[^\n]*\((\d+)\s*Days\)', generated_content, re.IGNORECASE)
            sr_match = re.search(r'Day\s+(\d+)\s+to\s+Day\s+(\d+)[^\n]*Shadow\s*Resourcing', generated_content, re.IGNORECASE)
            lr_match = re.search(r'Day\s+(\d+)\s+and\s+onwards', generated_content, re.IGNORECASE)

            if asmt_match and not has_asmt:
                d_start, d_end, d_cnt = asmt_match.group(1), asmt_match.group(2), int(asmt_match.group(3))
                hrs = d_cnt * 24
                topics.append({
                    "day_label": f"Day {d_start} to Day {d_end}",
                    "topic_name": "Mandatory Final Assessment Evaluation Window",
                    "estimated_duration_hours": f"{hrs} Hours ({d_cnt} Days)"
                })

            if sr_match and not has_sr:
                sr_start, sr_end = sr_match.group(1), sr_match.group(2)
                sr_days = max(1, int(sr_end) - int(sr_start) + 1)
                sr_weeks = max(1, sr_days // 7)
                hrs = sr_days * 24
                topics.append({
                    "day_label": f"Day {sr_start} to Day {sr_end} (Shadow Phase)",
                    "topic_name": "Practical Shadow Experience & Hands-on Ticket Resolution",
                    "estimated_duration_hours": f"{hrs} Hours ({sr_days} Days / {sr_weeks} Weeks)"
                })

            if lr_match and not has_lr:
                lr_start = lr_match.group(1)
                topics.append({
                    "day_label": f"Day {lr_start} onwards (Lead Phase)",
                    "topic_name": "Independent Project Leadership & Transition Completion",
                    "estimated_duration_hours": "Ongoing (Lead Phase)"
                })

        # Clear existing topics if this is a resync
        execute_write("DELETE FROM plan_topics WHERE plan_id = %s", (plan_id,))
        
        count = 0
        for item in topics:
            query = "INSERT INTO plan_topics (plan_id, day_label, topic_name, estimated_duration_hours) VALUES (%s, %s, %s, %s)"
            execute_write(query, (plan_id, item.get('day_label', 'General'), item.get('topic_name'), item.get('estimated_duration_hours', 'N/A')))
            count += 1
            
        return count
    except Exception as e:
        logging.warning(f"Failed to extract topics for plan {plan_id}: {e}")
        return 0

def add_topic_service(plan_id, day_label, topic_name, estimated_duration_hours="N/A"):
    query = "INSERT INTO plan_topics (plan_id, day_label, topic_name, estimated_duration_hours) VALUES (%s, %s, %s, %s)"
    topic_id = execute_write(query, (plan_id, day_label or 'General', topic_name, estimated_duration_hours or 'N/A'))
    return topic_id

def update_topic_service(topic_id, day_label=None, topic_name=None, estimated_duration_hours=None):
    updates = []
    params = []
    if day_label is not None:
        updates.append("day_label = %s")
        params.append(day_label)
    if topic_name is not None:
        updates.append("topic_name = %s")
        params.append(topic_name)
    if estimated_duration_hours is not None:
        updates.append("estimated_duration_hours = %s")
        params.append(estimated_duration_hours)
    
    if not updates:
        return False
        
    params.append(topic_id)
    query = f"UPDATE plan_topics SET {', '.join(updates)} WHERE id = %s"
    execute_write(query, tuple(params))
    return True

def delete_topic_service(topic_id):
    query = "DELETE FROM plan_topics WHERE id = %s"
    execute_write(query, (topic_id,))
    return True

def extract_plan_info_from_doc_service(files_input):
    import os
    import re
    import json
    import logging

    if not isinstance(files_input, list):
        files_input = [files_input]

    combined_text = ""
    first_filename = ""

    for file_storage in files_input:
        filename = file_storage.filename or ""
        if not first_filename and filename:
            first_filename = filename
        ext = os.path.splitext(filename)[1].lower()
        doc_text = ""

        if ext == '.pdf':
            try:
                import pypdf
                pdf_reader = pypdf.PdfReader(file_storage)
                for page in pdf_reader.pages:
                    page_text = page.extract_text()
                    if page_text:
                        doc_text += page_text + "\n"
            except Exception as e:
                logging.error(f"Error reading PDF {filename}: {e}")
                raise Exception(f"Failed to parse PDF file ({filename}): {str(e)}")
        elif ext in ['.docx', '.doc', '.docs']:
            try:
                import docx
                doc = docx.Document(file_storage)
                for para in doc.paragraphs:
                    if para.text and para.text.strip():
                        doc_text += para.text.strip() + "\n"
                for table in doc.tables:
                    for row in table.rows:
                        row_cells = [cell.text.strip() for cell in row.cells if cell.text and cell.text.strip()]
                        if row_cells:
                            doc_text += " | ".join(row_cells) + "\n"
            except Exception as e:
                logging.error(f"Error reading DOC/DOCX {filename}: {e}")
                raise Exception(f"Failed to parse Word document ({filename}): {str(e)}")
        elif ext == '.txt':
            doc_text = file_storage.read().decode('utf-8', errors='ignore')
        else:
            raise Exception(f"Unsupported file type for {filename}. Only PDF (.pdf) and Word documents (.doc, .docx) are allowed.")

        doc_text = doc_text.strip()
        if doc_text:
            combined_text += f"\n--- Document: {filename} ---\n{doc_text}\n"

    combined_text = combined_text.strip()
    if not combined_text:
        raise Exception("The uploaded document(s) are empty or text could not be extracted.")

    prompt = load_prompt(
        "plan_document_analysis.txt",
        file_count=len(files_input),
        combined_text=combined_text[:35000]
    )

    llm_res = call_llm(prompt)
    extracted_app_name = ""
    extracted_scope = ""

    if llm_res and isinstance(llm_res, str):
        match = re.search(r'\{.*\}', llm_res, re.DOTALL)
        if match:
            try:
                extracted = json.loads(match.group(0))
                extracted_app_name = str(extracted.get("application_name", "")).strip()
                extracted_scope = str(extracted.get("scope_description", "")).strip()
            except Exception as e:
                logging.error(f"Failed to parse LLM json regex match: {e}")

    # Helper function to clean application name
    def clean_name(raw_name):
        if not raw_name:
            return ""
        cleaned = re.sub(r'^(file|doc|document)\s*\d*[:\s-]*', '', raw_name, flags=re.IGNORECASE)
        cleaned = re.sub(r'\.(pdf|docx?|txt)$', '', cleaned, flags=re.IGNORECASE)
        cleaned = cleaned.replace('_', ' ').replace('-', ' ').strip()
        return cleaned.title()

    if not extracted_app_name or re.match(r'^(file|doc|document)\s*\d*', extracted_app_name, re.IGNORECASE):
        proj_match = re.search(r'(?:Project|System|Application)\s*Name[:\s-]+([^\n\r]+)', combined_text, re.IGNORECASE)
        if proj_match:
            extracted_app_name = clean_name(proj_match.group(1).strip())
        elif first_filename:
            extracted_app_name = clean_name(first_filename)
        else:
            extracted_app_name = "Hospital Management System"
    else:
        extracted_app_name = clean_name(extracted_app_name)

    if not extracted_scope or extracted_scope.startswith("--- Document:"):
        clean_lines = re.sub(r'---\s*Document:[^\n]+\n?', '', combined_text)
        clean_lines = re.sub(r'File\s*\d+:[^\n]+\n?', '', clean_lines)
        lines = [l.strip() for l in clean_lines.splitlines() if l.strip()]
        
        topics = []
        skip_words = {'contents', 'project name', 'project overview', 'project scope', 'functional modules', 'table of contents'}
        for line in lines:
            c_line = re.sub(r'^\d+[\.\)]\s*', '', line)
            c_line = re.sub(r'^[\-\*•]\s*', '', c_line).strip()
            if c_line and len(c_line) < 80 and c_line.lower() not in skip_words:
                if c_line not in topics and not c_line.lower().startswith('---'):
                    topics.append(c_line)
        if topics:
            extracted_scope = ", ".join(topics[:25])
        else:
            extracted_scope = re.sub(r'\s+', ' ', clean_lines[:500]).strip()

    return {
        "application_name": extracted_app_name or "Hospital Management System",
        "scope_description": extracted_scope
    }

def recalculate_plan_timeline_service(plan_id, new_assessment_days):
    """Dynamically update plan markdown text and topics in DB when manager saves new assessment window days."""
    try:
        plan_res = execute_query("SELECT generated_content, project_config FROM kt_plans WHERE id = %s", (plan_id,))
        if not plan_res or not plan_res[0].get('generated_content'):
            return
        
        content = plan_res[0]['generated_content']
        proj_config = plan_res[0].get('project_config')
        if proj_config and isinstance(proj_config, str):
            try:
                proj_config = json.loads(proj_config)
            except Exception:
                proj_config = {}
        elif not isinstance(proj_config, dict):
            proj_config = {}
            
        proj_config['final_deadline_extension_days'] = new_assessment_days

        # Knowledge Acquisition (KA) Phase ends at Day 15
        ka_last_day = 15

        asmt_start = ka_last_day + 1
        asmt_end = ka_last_day + new_assessment_days
        sr_start = asmt_end + 1
        sr_end = sr_start + 13 # 2 weeks (14 days)
        lr_start = sr_end + 1

        # Replace Final Assessment Window section in markdown text cleanly
        content = re.sub(
            r'####?\s*Day\s+\d+\s+to\s+Day\s+\d+:[^\n]*Final\s*Assessment[^\n]*\((\d+)\s*Days\)',
            f'#### Day {asmt_start} to Day {asmt_end}: Final Assessment Evaluation Window ({new_assessment_days} Days)',
            content,
            flags=re.IGNORECASE
        )
        content = re.sub(
            r'•\s*Mandatory\s*Final\s*Assessment\s*Evaluation\s*Window[^\n]*:\s*\d+\s*Days\.?',
            f'• Mandatory Final Assessment Evaluation Window allocated by Manager: {new_assessment_days} Days.',
            content,
            flags=re.IGNORECASE
        )

        # Update SR Phase day range in content
        content = re.sub(
            r'####?\s*Day\s+\d+\s+to\s+Day\s+\d+[^\n]*Shadow\s*Resourcing[^\n]*',
            f'#### Day {sr_start} to Day {sr_end} (Week 1-2: Shadow Resourcing Phase)',
            content,
            flags=re.IGNORECASE
        )

        # Update LR Phase day range in content
        content = re.sub(
            r'####?\s*Day\s+\d+\s+and\s+onwards:?',
            f'#### Day {lr_start} and onwards:',
            content,
            flags=re.IGNORECASE
        )

        # Write updated content and project_config back to kt_plans table
        execute_write(
            "UPDATE kt_plans SET generated_content = %s, project_config = %s, final_deadline_extension_days = %s WHERE id = %s",
            (content, json.dumps(proj_config), new_assessment_days, plan_id)
        )

        # Update or insert post-KA phase topics in plan_topics table directly (Instant SQL execution)
        asmt_hrs = new_assessment_days * 24
        asmt_label = f"Day {asmt_start} to Day {asmt_end}"
        asmt_dur = f"{asmt_hrs} Hours ({new_assessment_days} Days)"

        sr_days = 14
        sr_hrs = sr_days * 24
        sr_label = f"Day {sr_start} to Day {sr_end} (Shadow Phase)"
        sr_dur = f"{sr_hrs} Hours ({sr_days} Days / 2 Weeks)"

        lr_label = f"Day {lr_start} onwards (Lead Phase)"

        # Check and update/insert Assessment topic row
        asmt_row = execute_query("SELECT id FROM plan_topics WHERE plan_id = %s AND (topic_name LIKE '%%Assessment%%' OR day_label LIKE '%%Final Assessment%%')", (plan_id,))
        if asmt_row:
            execute_write("UPDATE plan_topics SET day_label = %s, estimated_duration_hours = %s WHERE id = %s", (asmt_label, asmt_dur, asmt_row[0]['id']))
        else:
            execute_write("INSERT INTO plan_topics (plan_id, day_label, topic_name, estimated_duration_hours) VALUES (%s, %s, %s, %s)", (plan_id, asmt_label, "Mandatory Final Assessment Evaluation Window", asmt_dur))

        # Check and update/insert Shadow Resourcing topic row
        sr_row = execute_query("SELECT id FROM plan_topics WHERE plan_id = %s AND (topic_name LIKE '%%Shadow%%' OR day_label LIKE '%%Shadow%%')", (plan_id,))
        if sr_row:
            execute_write("UPDATE plan_topics SET day_label = %s, estimated_duration_hours = %s WHERE id = %s", (sr_label, sr_dur, sr_row[0]['id']))
        else:
            execute_write("INSERT INTO plan_topics (plan_id, day_label, topic_name, estimated_duration_hours) VALUES (%s, %s, %s, %s)", (plan_id, sr_label, "Practical Shadow Experience & Hands-on Ticket Resolution", sr_dur))

        # Check and update/insert Lead Resourcing topic row
        lr_row = execute_query("SELECT id FROM plan_topics WHERE plan_id = %s AND (topic_name LIKE '%%Lead%%' OR day_label LIKE '%%Lead%%')", (plan_id,))
        if lr_row:
            execute_write("UPDATE plan_topics SET day_label = %s WHERE id = %s", (lr_label, lr_row[0]['id']))
        else:
            execute_write("INSERT INTO plan_topics (plan_id, day_label, topic_name, estimated_duration_hours) VALUES (%s, %s, %s, %s)", (plan_id, lr_label, "Independent Project Leadership & Transition Completion", "Ongoing (Lead Phase)"))

    except Exception as e:
        logging.error(f"Error in recalculate_plan_timeline_service: {e}")


