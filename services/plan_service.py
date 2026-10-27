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

def get_reverse_kt_days_from_config(project_config):
    """
    Extract allocated Reverse KT duration (in days) from project_config.
    Checks track/module specific configuration using _meta if available,
    or falls back to general tracks or phaseDurations.
    """
    if not project_config:
        return None
        
    if isinstance(project_config, str):
        try:
            project_config = json.loads(project_config)
        except Exception:
            return None

    if not isinstance(project_config, dict):
        return None

    _meta = project_config.get('_meta', {})
    track_id = _meta.get('trackId')
    module_id = _meta.get('moduleId')
    
    options = {}
    durations = {}
    tracks = project_config.get('tracks', [])
    if track_id and tracks:
        for t in tracks:
            if str(t.get('id')) == str(track_id):
                if module_id:
                    for m in t.get('modules', []):
                        if str(m.get('id')) == str(module_id):
                            options = m.get('options', {})
                            durations = m.get('durations', {})
                            break
                else:
                    options = t.get('options', {})
                    durations = t.get('durations', {})
                break
    elif tracks:
        options = tracks[0].get('options', {})
        durations = tracks[0].get('durations', {})
    else:
        options = project_config.get('phaseSelected', {})
        durations = project_config.get('phaseDurations', {})

    # If options explicitly says reverse_kt is disabled (False) and no duration entered, return None
    if options and not options.get('reverse_kt') and not options.get('rkt_assessment'):
        if not (durations and durations.get('reverse_kt') and str(durations.get('reverse_kt')).isdigit() and int(durations.get('reverse_kt')) > 0):
            return None

    if durations:
        rkt_val = durations.get('reverse_kt')
        if rkt_val is not None and str(rkt_val).strip() != '':
            try:
                val_int = int(rkt_val)
                if val_int > 0:
                    return val_int
            except (ValueError, TypeError):
                pass

    phase_durs = project_config.get('phaseDurations', {})
    if phase_durs:
        rkt_val = phase_durs.get('reverse_kt')
        if rkt_val is not None and str(rkt_val).strip() != '':
            try:
                val_int = int(rkt_val)
                if val_int > 0:
                    return val_int
            except (ValueError, TypeError):
                pass

    saved_days = project_config.get('final_deadline_extension_days')
    if saved_days is not None and str(saved_days).isdigit() and int(saved_days) > 0 and int(saved_days) != 90:
        return int(saved_days)

    # Fallback to date-based allocation if durations dictionary was not directly filled
    start_str = project_config.get('startDate') or project_config.get('start_date')
    end_str = project_config.get('endDate') or project_config.get('end_date')
    if start_str and end_str and options and (options.get('reverse_kt') or options.get('rkt_assessment')):
        try:
            from datetime import datetime
            s_d = datetime.strptime(str(start_str)[:10], '%Y-%m-%d')
            e_d = datetime.strptime(str(end_str)[:10], '%Y-%m-%d')
            total_days = max(1, (e_d - s_d).days + 1)
            sel = [p for p in ['ka', 'reverse_kt', 'shadow_resourcing', 'lead_resourcing'] if options.get(p)]
            if 'reverse_kt' in sel:
                if len(sel) == 1:
                    return total_days
                elif len(sel) > 1:
                    ka_share = int(round(total_days * 0.70)) if 'ka' in sel else 0
                    rem = total_days - ka_share
                    other_cnt = len(sel) - (1 if 'ka' in sel else 0)
                    if other_cnt > 0:
                        calc_share = max(1, rem // other_cnt)
                        return calc_share
        except Exception:
            pass

    return None

def get_session_allocation_text(project_config):
    if not project_config:
        return ""
        
    _meta = project_config.get('_meta', {})
    track_id = _meta.get('trackId')
    module_id = _meta.get('moduleId')
    
    options = {}
    durations = {}
    inputs = {}
    tracks = project_config.get('tracks', [])
    if track_id:
        for t in tracks:
            if t.get('id') == track_id:
                if module_id:
                    for m in t.get('modules', []):
                        if m.get('id') == module_id:
                            options = m.get('options', {})
                            durations = m.get('durations', {})
                            inputs = m.get('inputs', {})
                            break
                else:
                    options = t.get('options', {})
                    durations = t.get('durations', {})
                    inputs = t.get('inputs', {})
                break
    elif tracks:
        options = tracks[0].get('options', {})
        durations = tracks[0].get('durations', {})
        inputs = tracks[0].get('inputs', {})
    else:
        options = project_config.get('phaseSelected', {})
        durations = project_config.get('phaseDurations', {})
        inputs = project_config.get('inputs', {})
                
    if not options:
        return ""
        
    has_ka = bool(options.get('ka'))
    has_rkt = bool(options.get('reverse_kt'))
    has_ss = bool(options.get('shadow_resourcing'))
    has_ls = bool(options.get('lead_resourcing'))
    
    selected_phases = []
    if has_ka: selected_phases.append("Knowledge Acquisition")
    if has_rkt: selected_phases.append("Reverse KT")
    if has_ss: selected_phases.append("Shadow Support")
    if has_ls: selected_phases.append("Lead Support")
    
    num_phases = len(selected_phases)
    if num_phases == 0:
        return ""

    phase_keys = {
        "Knowledge Acquisition": "ka",
        "Reverse KT": "reverse_kt",
        "Shadow Support": "shadow_resourcing",
        "Lead Support": "lead_resourcing"
    }

    # Extract total duration from durations dictionary
    total_dur_days = 0
    if durations:
        for k in ['ka', 'reverse_kt', 'shadow_resourcing', 'lead_resourcing']:
            val = durations.get(k)
            if val is not None and str(val).isdigit():
                total_dur_days += int(val)

    expected_sessions = project_config.get('expectedSessions')
    if expected_sessions:
        try:
            expected_sessions = int(expected_sessions)
        except ValueError:
            expected_sessions = total_dur_days
    else:
        expected_sessions = total_dur_days

    allocations = {}
    can_use_direct_durations = durations and all(
        durations.get(phase_keys[p]) is not None and str(durations.get(phase_keys[p])).isdigit()
        for p in selected_phases
    )

    if can_use_direct_durations:
        for p in selected_phases:
            allocations[p] = int(durations[phase_keys[p]])
        if expected_sessions == 0:
            expected_sessions = sum(allocations.values())
    elif expected_sessions > 0:
        if has_ka:
            if num_phases == 1:
                ka_ratio = 1.0
            else:
                # 4 phases -> 70:10:10:10, 3 phases -> 70:15:15, 2 phases -> 70:30
                ka_ratio = 0.70
                
            allocations["Knowledge Acquisition"] = int(round(expected_sessions * ka_ratio))
            
            others_count = num_phases - 1
            if others_count > 0:
                remaining_sessions = expected_sessions - allocations["Knowledge Acquisition"]
                other_phases = [p for p in selected_phases if p != "Knowledge Acquisition"]
                for i, p in enumerate(other_phases):
                    share = remaining_sessions // others_count
                    if i < remaining_sessions % others_count:
                        share += 1
                    allocations[p] = share
        else:
            for i, p in enumerate(selected_phases):
                share = expected_sessions // num_phases
                if i < expected_sessions % num_phases:
                    share += 1
                allocations[p] = share
    else:
        for p in selected_phases:
            allocations[p] = 1
        expected_sessions = len(selected_phases)
            
    start_date = project_config.get('startDate') or project_config.get('start_date') or 'Not Specified'
    end_date = project_config.get('endDate') or project_config.get('end_date') or 'Not Specified'

    phase_criteria = {
        "Knowledge Acquisition": {
            "entry": [
                ("kt_doc", "KT Plan Ready"),
                ("stakeholder_map", "Stakeholder Mapping Ready")
            ],
            "exit": [
                ("project_kt_doc_ready", "Project KT Document Ready"),
                ("sud_mandatory", "SUD Mandatory")
            ]
        },
        "Reverse KT": {
            "entry": [
                ("ka_completed", "Knowledge Acquisition Completed"),
                ("rkt_project_kt_doc", "Project KT Document Ready")
            ],
            "exit": [
                ("rkt_assessment", "Assessment Passed (≥80%)")
            ]
        },
        "Shadow Support": {
            "entry": [
                ("assessment_80", "Assessment (80% Above)"),
                ("sud_doc_upload", "SUD Document Uploaded or Not")
            ],
            "exit": [
                ("ticket_resolving", "Need to be involved in ticket resolving")
            ]
        },
        "Lead Support": {
            "entry": [
                ("lr_ticket_resolving", "Need to be involved in ticket resolving")
            ],
            "exit": []
        }
    }
            
    dist_text = f"\n    [CRITICAL INSTRUCTION: Track Configuration, Durations & Phase Criteria]\n"
    dist_text += f"    Total Duration: {expected_sessions} days\n"
    dist_text += f"    Planned KT Start Date: {start_date}\n"
    dist_text += f"    Planned KT End Date: {end_date}\n"
    dist_text += f"    You MUST strictly adhere to the following phase durations and document all configured criteria in each phase:\n"
    for p in selected_phases:
        p_key = phase_keys[p]
        dur_val = durations.get(p_key) if (durations and durations.get(p_key) is not None) else allocations.get(p)
        days_str = f" ({dur_val} days)" if dur_val is not None else ""
        dist_text += f"    - {p}{days_str}:\n"
        
        cfg = phase_criteria.get(p, {})
        entry_list = []
        for cid, clabel in cfg.get("entry", []):
            if options.get(cid):
                inp = inputs.get(cid)
                if inp:
                    entry_list.append(f"{clabel} (Target: {inp})")
                else:
                    entry_list.append(clabel)
                    
        exit_list = []
        for cid, clabel in cfg.get("exit", []):
            if options.get(cid):
                inp = inputs.get(cid)
                if inp:
                    exit_list.append(f"{clabel} (Target: {inp})")
                else:
                    exit_list.append(clabel)
                    
        if entry_list:
            dist_text += f"      * Entry Criteria: {', '.join(entry_list)}\n"
        if exit_list:
            dist_text += f"      * Exit Criteria: {', '.join(exit_list)}\n"
        
    rkt_days = durations.get('reverse_kt') if (durations and durations.get('reverse_kt') is not None) else allocations.get('Reverse KT')
    if has_rkt and rkt_days:
        try:
            rkt_days_int = int(rkt_days)
            if rkt_days_int > 0:
                dist_text += f"\n    [SPECIAL ASSESSMENT TIMING RULE FOR REVERSE KT]\n"
                dist_text += f"    Reverse KT is configured with duration: {rkt_days_int} days (Exit Criteria: 'Assessment Passed (>=80%)').\n"
                dist_text += f"    The Final Assessment Evaluation Window timing MUST be allocated EXACTLY {rkt_days_int} days (Day K+1 to Day K+{rkt_days_int}), strictly matching the Reverse KT allocated duration.\n"
        except (ValueError, TypeError):
            pass

    return dist_text

def generate_plan_service(application_name, scope_description, plan_type, user_email=None, user_full_name=None, user_role=None, reverse_kt_focus=None, project_config=None, project_id=None, plan_content=None):
    created_by = None
    if user_email and user_full_name and user_role:
        created_by = resolve_stakeholder_for_user(user_email, user_full_name, user_role)

    tech_stack = ""
    if project_config:
        tech_stack = project_config.get('techStack') or project_config.get('tech_stack') or ""
    if not tech_stack:
        tech_stack = "Relevant technologies as specified in track scopes"

    focus_text = f"\n    Reverse KT Focus Area: {reverse_kt_focus}" if reverse_kt_focus and plan_type == 'Reverse-KT' else ""
    session_allocation_instruction = get_session_allocation_text(project_config)
    project_config_text = f"\n    Project Configuration Details:\n{json.dumps(project_config, indent=2)}\n{session_allocation_instruction}" if project_config else ""
    
    start_date = "Not Specified"
    end_date = "Not Specified"
    if project_config:
        start_date = project_config.get('startDate') or project_config.get('start_date') or 'Not Specified'
        end_date = project_config.get('endDate') or project_config.get('end_date') or 'Not Specified'

    # Determine assessment duration based on Reverse KT allocated duration
    rkt_days = get_reverse_kt_days_from_config(project_config)
    assessment_days = rkt_days if (rkt_days and rkt_days > 0) else 3

    # If full plan content is supplied from an uploaded document, bypass LLM generation
    if plan_content and isinstance(plan_content, str) and plan_content.strip():
        generated_content = plan_content.strip()
    else:
        prompt = load_prompt(
            "plan_generation.txt",
            plan_type=plan_type,
            application_name=application_name,
            tech_stack=tech_stack,
            start_date=start_date,
            end_date=end_date,
            scope_description=scope_description,
            focus_text=focus_text,
            project_config_text=project_config_text,
            assessment_days=assessment_days
        )
        # Call LLM
        generated_content = call_llm(prompt)
        generated_content = sanitize_and_resolve_timeline(generated_content, assessment_days)
    
    if project_config:
        project_config['final_deadline_extension_days'] = assessment_days

    # Save to DB as draft with final_deadline_extension_days
    query = """
        INSERT INTO kt_plans (application_name, scope_description, plan_type, generated_content, status, created_by, project_config, project_id, final_deadline_extension_days)
        VALUES (%s, %s, %s, %s, 'draft', %s, %s, %s, %s)
    """
    params = (application_name, scope_description, plan_type, generated_content, created_by, json.dumps(project_config) if project_config else None, project_id, assessment_days)
    plan_id = execute_write(query, params)
    
    # Extract topics
    extract_and_save_topics(plan_id, generated_content, default_asmt_days=assessment_days)

    # Enforce exact timeline synchronization across content and topics
    recalculate_plan_timeline_service(plan_id, assessment_days)

    # Retrieve updated markdown content to return
    updated = execute_query("SELECT generated_content FROM kt_plans WHERE id = %s", (plan_id,))
    final_content = updated[0]['generated_content'] if updated else generated_content

    return {
        "id": plan_id,
        "generated_content": final_content,
        "status": "draft"
    }

def generate_tentative_plan_service(application_name, scope_description, plan_type, user_email=None, user_full_name=None, user_role=None, reverse_kt_focus=None, project_config=None):
    """Generates tentative KT plan markdown via AI WITHOUT saving it to the kt_plans database."""
    tech_stack = ""
    if project_config:
        tech_stack = project_config.get('techStack') or project_config.get('tech_stack') or ""
    if not tech_stack:
        tech_stack = "Relevant technologies as specified in track scopes"

    focus_text = f"\n    Reverse KT Focus Area: {reverse_kt_focus}" if reverse_kt_focus and plan_type == 'Reverse-KT' else ""
    session_allocation_instruction = get_session_allocation_text(project_config)
    project_config_text = f"\n    Project Configuration Details:\n{json.dumps(project_config, indent=2)}\n{session_allocation_instruction}" if project_config else ""
    
    start_date = "Not Specified"
    end_date = "Not Specified"
    if project_config:
        start_date = project_config.get('startDate') or project_config.get('start_date') or 'Not Specified'
        end_date = project_config.get('endDate') or project_config.get('end_date') or 'Not Specified'

    # Determine assessment duration based on Reverse KT allocated duration
    rkt_days = get_reverse_kt_days_from_config(project_config)
    assessment_days = rkt_days if (rkt_days and rkt_days > 0) else 3

    prompt = load_prompt(
        "plan_generation.txt",
        plan_type=plan_type,
        application_name=application_name,
        tech_stack=tech_stack,
        start_date=start_date,
        end_date=end_date,
        scope_description=scope_description,
        focus_text=focus_text,
        project_config_text=project_config_text,
        assessment_days=assessment_days
    )

    generated_content = call_llm(prompt)
    generated_content = sanitize_and_resolve_timeline(generated_content, assessment_days)

    return {
        "generated_content": generated_content,
        "application_name": application_name,
        "plan_type": plan_type,
        "scope_description": scope_description
    }

def sanitize_and_resolve_timeline(content, assessment_days=None):
    """
    Deterministically resolves any {K...} or Day (K...) placeholders in plan markdown text
    into concrete day numbers based on the actual KA phase duration.
    Also ensures the Post-KA phase timelines (Final Assessment, Shadow Resourcing, Lead Resourcing)
    have exact, seamless day ranges.
    """
    if not content or not isinstance(content, str):
        return content

    import re

    # 1. Determine ka_last_day (K) from markdown text before Post-KA phases
    ka_part = re.split(r'#{0,4}\s*Final\s*Assessment|#{0,4}\s*Shadow\s*Resourcing|#{0,4}\s*Lead\s*Resourcing', content, flags=re.IGNORECASE)[0]
    day_matches = re.findall(r'Day\s+(\d+)', ka_part, re.IGNORECASE)
    ka_last_day = max([int(d) for d in day_matches]) if day_matches else 1

    # If assessment_days not provided, attempt to parse from content or default to 3
    if assessment_days is None:
        asmt_day_match = re.search(r'Final\s*Assessment\s*Evaluation\s*Window[^\n]*?:\s*(\d+)\s*Days', content, re.IGNORECASE)
        rkt_match = re.search(r'Reverse\s*KT[^\n]*?(\d+)\s*Days', content, re.IGNORECASE)
        if asmt_day_match and int(asmt_day_match.group(1)) != 90:
            assessment_days = int(asmt_day_match.group(1))
        elif rkt_match and int(rkt_match.group(1)) != 90:
            assessment_days = int(rkt_match.group(1))
        elif asmt_day_match:
            assessment_days = int(asmt_day_match.group(1))
        else:
            assessment_days = 3

    asmt_start = ka_last_day + 1
    asmt_end = ka_last_day + assessment_days
    sr_start = asmt_end + 1
    sr_end = sr_start + 13  # 14 days / 2 weeks
    lr_start = sr_end + 1

    # 2. Update Final Assessment section
    def update_asmt(m):
        header = m.group(1)
        body = m.group(2)
        body = re.sub(
            r'([•\*]?\s*\**Timeline:\**\s*)[^\n]+',
            rf'\g<1>Day {asmt_start} to Day {asmt_end} ({assessment_days} Days)',
            body,
            count=1,
            flags=re.IGNORECASE
        )
        body = re.sub(
            r'((?:Mandatory\s*)?Final\s*Assessment\s*Evaluation\s*Window[^\n]*?:\s*)\d+(\s*Days)',
            rf'\g<1>{assessment_days}\g<2>',
            body,
            flags=re.IGNORECASE
        )
        return header + body

    content = re.sub(
        r'(#{0,4}\s*Final\s*Assessment[^\n]*\n)([\s\S]*?)(?=\n#{0,4}\s*Shadow|\n#{0,4}\s*Lead|\n##|\Z)',
        update_asmt,
        content,
        flags=re.IGNORECASE
    )

    # 3. Update Shadow Resourcing section
    def update_sr(m):
        header = m.group(1)
        body = m.group(2)
        body = re.sub(
            r'([•\*]?\s*\**Timeline:\**\s*)[^\n]+',
            rf'\g<1>Day {sr_start} to Day {sr_end} (Week 1-2: 14 Days / 2 Weeks)',
            body,
            count=1,
            flags=re.IGNORECASE
        )
        return header + body

    content = re.sub(
        r'(#{0,4}\s*Shadow\s*Resourcing[^\n]*\n)([\s\S]*?)(?=\n#{0,4}\s*Lead|\n##|\Z)',
        update_sr,
        content,
        flags=re.IGNORECASE
    )

    # 4. Update Lead Resourcing section
    def update_lr(m):
        header = m.group(1)
        body = m.group(2)
        body = re.sub(
            r'([•\*]?\s*\**Timeline:\**\s*)[^\n]+',
            rf'\g<1>Day {lr_start} onwards',
            body,
            count=1,
            flags=re.IGNORECASE
        )
        return header + body

    content = re.sub(
        r'(#{0,4}\s*Lead\s*Resourcing[^\n]*\n)([\s\S]*?)(?=\n##|\Z)',
        update_lr,
        content,
        flags=re.IGNORECASE
    )

    # 5. Global regex fallback to safely evaluate ANY remaining {K+...} or (K+...) expressions anywhere in the content
    def eval_k_expr(m):
        prefix = m.group(1) or ""
        expr = m.group(2)
        try:
            clean_expr = re.sub(r'[Kk]', str(ka_last_day), expr)
            if re.match(r'^[\d\+\-\s]+$', clean_expr):
                val = eval(clean_expr)
                return f"{prefix}{val}"
        except Exception:
            pass
        return m.group(0)

    content = re.sub(r'(Day\s+)?[\{\(]([Kk][\d\+\-\s]*)[\}\)]', eval_k_expr, content)
    content = re.sub(r'Day\s+([Kk]\s*[\+\-]\s*\d+)', lambda m: f"Day {eval(re.sub(r'[Kk]', str(ka_last_day), m.group(1)))}", content)

    return content

def convert_markdown_to_docx(content, application_name="Application", plan_type="KT"):
    """Converts KT plan markdown to a professionally formatted Word Document (.docx)."""
    import io
    import re
    from docx import Document
    from docx.shared import Inches, Pt, RGBColor
    from datetime import datetime

    # Ensure timeline placeholders like {K+1} are resolved to actual day numbers
    content = sanitize_and_resolve_timeline(content)

    doc = Document()
    
    # Margins
    for section in doc.sections:
        section.top_margin = Inches(0.8)
        section.bottom_margin = Inches(0.8)
        section.left_margin = Inches(0.8)
        section.right_margin = Inches(0.8)

    # Title
    title_p = doc.add_heading(level=0)
    title_run = title_p.add_run(f"Tentative {plan_type} Plan: {application_name}")
    title_run.font.size = Pt(20)
    title_run.font.color.rgb = RGBColor(227, 82, 5) # Primary orange

    # Subtitle / Metadata
    meta_p = doc.add_paragraph()
    meta_p.paragraph_format.space_after = Pt(8)
    meta_p.add_run("Track / Application: ").bold = True
    meta_p.add_run(f"{application_name}    |    ")
    meta_p.add_run("Plan Type: ").bold = True
    meta_p.add_run(f"{plan_type}    |    ")
    meta_p.add_run("Generated Date: ").bold = True
    meta_p.add_run(datetime.now().strftime("%Y-%m-%d"))

    # Notice
    note_p = doc.add_paragraph()
    note_p.paragraph_format.space_after = Pt(14)
    note_run = note_p.add_run("[Tentative KT Plan Document - Upload this document in 'Generate Plan with Document' to set it into the application]")
    note_run.italic = True
    note_run.font.size = Pt(9.5)
    note_run.font.color.rgb = RGBColor(120, 120, 120)

    # Process markdown lines
    lines = content.splitlines()
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        if stripped.startswith('#### '):
            h = doc.add_heading(stripped[5:].strip(), level=3)
            h.paragraph_format.space_before = Pt(8)
            h.paragraph_format.space_after = Pt(2)
        elif stripped.startswith('### '):
            h = doc.add_heading(stripped[4:].strip(), level=2)
            h.paragraph_format.space_before = Pt(10)
            h.paragraph_format.space_after = Pt(3)
        elif stripped.startswith('## '):
            h = doc.add_heading(stripped[3:].strip(), level=1)
            h.paragraph_format.space_before = Pt(12)
            h.paragraph_format.space_after = Pt(4)
        elif stripped.startswith('# '):
            h = doc.add_heading(stripped[2:].strip(), level=1)
            h.paragraph_format.space_before = Pt(14)
            h.paragraph_format.space_after = Pt(4)
        elif re.match(r'^Day\s+\d+[:\s]', stripped, re.IGNORECASE):
            h = doc.add_heading(stripped, level=3)
            h.paragraph_format.space_before = Pt(8)
            h.paragraph_format.space_after = Pt(2)
        elif stripped.startswith(('•', '-')) or (stripped.startswith('*') and not stripped.startswith('**')):
            t = stripped
            while True:
                new_t = re.sub(r'^(?:[•\-\ufffd]|\*(?!\*))\s*', '', t).strip()
                if new_t == t:
                    break
                t = new_t
            bullet_text = t
            p = doc.add_paragraph(style='List Bullet')
            p.paragraph_format.space_before = Pt(1)
            p.paragraph_format.space_after = Pt(2)
            parts = re.split(r'(\*\*.*?\*\*)', bullet_text)
            for part in parts:
                if part.startswith('**') and part.endswith('**') and len(part) >= 4:
                    p.add_run(part[2:-2]).bold = True
                else:
                    p.add_run(part)
        elif stripped.startswith('(') and ('daily total' in stripped.lower() or 'timeline:' in stripped.lower()):
            p = doc.add_paragraph()
            p.paragraph_format.space_before = Pt(1)
            p.paragraph_format.space_after = Pt(5)
            run = p.add_run(stripped)
            run.italic = True
            run.font.size = Pt(9.5)
            run.font.color.rgb = RGBColor(90, 90, 90)
        elif re.match(r'^\d+\.\s+', stripped):
            p = doc.add_paragraph()
            p.paragraph_format.space_before = Pt(4)
            p.paragraph_format.space_after = Pt(2)
            run = p.add_run(stripped)
            run.bold = True
        else:
            p = doc.add_paragraph()
            p.paragraph_format.space_before = Pt(2)
            p.paragraph_format.space_after = Pt(3)
            parts = re.split(r'(\*\*.*?\*\*)', stripped)
            for part in parts:
                if part.startswith('**') and part.endswith('**') and len(part) >= 4:
                    p.add_run(part[2:-2]).bold = True
                else:
                    p.add_run(part)

    bio = io.BytesIO()
    doc.save(bio)
    bio.seek(0)
    return bio

def parse_topics_directly_from_content(generated_content, default_asmt_days=3):
    """Deterministic Python parser that reads markdown text directly to extract all sub-topic rows."""
    import re
    topics = []
    
    # Split content by Day headers or Post-KA H4 headers
    day_blocks = re.split(r'\n(?=Day\s+\d+:|####\s*Final\s*Assessment|####\s*Shadow\s*Resourcing|####\s*Lead\s*Resourcing)', generated_content, flags=re.IGNORECASE)
    
    ka_last_day = 1
    
    for block in day_blocks:
        block_strip = block.strip()
        day_match = re.match(r'^(Day\s+(\d+)[^\n]*)', block_strip, re.IGNORECASE)
        if not day_match:
            continue
            
        full_day_header = day_match.group(1).strip()
        day_num = int(day_match.group(2))
        
        # Skip if this is a post-KA phase header wrongly formatted as Day X
        if 'assessment' in block_strip.lower() or 'shadow' in block_strip.lower() or 'lead' in block_strip.lower():
            continue
            
        ka_last_day = max(ka_last_day, day_num)
        
        # Find all bullet lines: • Sub-topic Name (N minutes)
        bullet_matches = re.findall(r'[•\*\-\ufffd]\s*([^\n\(]+?)\s*\((?:Part\s+\d+\s*-\s*)?(\d+)\s*minutes?\)', block_strip, re.IGNORECASE)
        
        if bullet_matches:
            for sub_name, mins_str in bullet_matches:
                sub_name_clean = sub_name.strip()
                if 'daily total' in sub_name_clean.lower() or 'timeline:' in sub_name_clean.lower():
                    continue
                mins = int(mins_str)
                hrs = round(mins / 60.0, 2)
                hrs_str = f"{hrs:g}"
                
                topics.append({
                    "day_label": full_day_header,
                    "topic_name": sub_name_clean,
                    "estimated_duration_hours": hrs_str
                })
        else:
            main_headings = re.findall(r'^\d+\.\s*([^\n]+)', block_strip, re.MULTILINE)
            for h_name in main_headings:
                h_clean = re.sub(r'\(continued\)', '', h_name, flags=re.IGNORECASE).strip()
                if h_clean and 'daily total' not in h_clean.lower() and 'timeline:' not in h_clean.lower():
                    topics.append({
                        "day_label": full_day_header,
                        "topic_name": h_clean,
                        "estimated_duration_hours": "2"
                    })

    # Post-KA Phases parsed directly from H4 headers or default
    asmt_match = re.search(r'####\s*Final\s*Assessment[\s\S]*?Timeline:\s*Day\s+(\d+)\s+to\s+Day\s+(\d+)\s*(?:\((\d+)\s*Days\))?', generated_content, re.IGNORECASE) or re.search(r'Timeline:\s*Day\s+(\d+)\s+to\s+Day\s+(\d+)\s*(?:\((\d+)\s*Days\))?', generated_content, re.IGNORECASE)
    sr_match = re.search(r'####\s*Shadow\s*Resourcing[\s\S]*?Timeline:\s*Day\s+(\d+)\s+to\s+Day\s+(\d+)', generated_content, re.IGNORECASE)
    lr_match = re.search(r'####\s*Lead\s*Resourcing[\s\S]*?Timeline:\s*Day\s+(\d+)\s+onwards', generated_content, re.IGNORECASE)

    if asmt_match:
        a_start, a_end = int(asmt_match.group(1)), int(asmt_match.group(2))
        a_days = int(asmt_match.group(3)) if (len(asmt_match.groups()) >= 3 and asmt_match.group(3)) else (a_end - a_start + 1)
        asmt_label = f"Day {a_start} to Day {a_end}"
        asmt_hrs = a_days * 24
        asmt_dur = f"{asmt_hrs} Hours ({a_days} Days)"
    else:
        a_days = default_asmt_days
        a_start = ka_last_day + 1
        a_end = ka_last_day + a_days
        asmt_label = f"Day {a_start} to Day {a_end}"
        asmt_dur = f"{a_days * 24} Hours ({a_days} Days)"

    topics.append({
        "day_label": asmt_label,
        "topic_name": "Mandatory Final Assessment Evaluation Window",
        "estimated_duration_hours": asmt_dur
    })

    if sr_match:
        s_start, s_end = int(sr_match.group(1)), int(sr_match.group(2))
        sr_label = f"Day {s_start} to Day {s_end} (Shadow Phase)"
        s_days = max(1, s_end - s_start + 1)
        sr_dur = f"{s_days * 24} Hours ({s_days} Days / 2 Weeks)"
    else:
        s_days = 14
        s_start = a_end + 1
        s_end = s_start + 13
        sr_label = f"Day {s_start} to Day {s_end} (Shadow Phase)"
        sr_dur = f"{s_days * 24} Hours ({s_days} Days / 2 Weeks)"

    topics.append({
        "day_label": sr_label,
        "topic_name": "Practical Shadow Experience & Hands-on Ticket Resolution",
        "estimated_duration_hours": sr_dur
    })

    if lr_match:
        l_start = int(lr_match.group(1))
        lr_label = f"Day {l_start} onwards (Lead Phase)"
    else:
        l_start = s_end + 1
        lr_label = f"Day {l_start} onwards (Lead Phase)"

    topics.append({
        "day_label": lr_label,
        "topic_name": "Independent Project Leadership & Transition Completion",
        "estimated_duration_hours": "Ongoing (Lead Phase)"
    })

    return topics

def ensure_post_ka_phase_order(topics, generated_content, ka_last_day=1, default_asmt_days=3):
    """Separate KA topics and Post-KA topics, then reconstruct Post-KA topics strictly as: Assessment -> Shadow -> Lead."""
    import re
    
    ka_topics = []
    
    for t in topics:
        d_lbl = str(t.get('day_label', ''))
        t_nm = str(t.get('topic_name', ''))
        full_str = (d_lbl + ' ' + t_nm).lower()
        
        if 'assessment' in full_str or 'shadow' in full_str or 'lead' in full_str:
            continue
        ka_topics.append(t)
            
    # Parse exact timeline numbers from generated_content markdown strictly from H4 headers if present
    asmt_match = re.search(r'####\s*Final\s*Assessment[\s\S]*?Timeline:\s*Day\s+(\d+)\s+to\s+Day\s+(\d+)\s*(?:\((\d+)\s*Days\))?', generated_content, re.IGNORECASE) or re.search(r'Timeline:\s*Day\s+(\d+)\s+to\s+Day\s+(\d+)\s*(?:\((\d+)\s*Days\))?', generated_content, re.IGNORECASE)
    sr_match = re.search(r'####\s*Shadow\s*Resourcing[\s\S]*?Timeline:\s*Day\s+(\d+)\s+to\s+Day\s+(\d+)', generated_content, re.IGNORECASE)
    lr_match = re.search(r'####\s*Lead\s*Resourcing[\s\S]*?Timeline:\s*Day\s+(\d+)\s+onwards', generated_content, re.IGNORECASE)

    # 1. Assessment Topic
    if asmt_match:
        a_start, a_end = int(asmt_match.group(1)), int(asmt_match.group(2))
        a_days = int(asmt_match.group(3)) if (len(asmt_match.groups()) >= 3 and asmt_match.group(3)) else (a_end - a_start + 1)
        asmt_label = f"Day {a_start} to Day {a_end}"
        asmt_dur = f"{a_days * 24} Hours ({a_days} Days)"
    else:
        a_days = default_asmt_days
        a_start = ka_last_day + 1
        a_end = ka_last_day + a_days
        asmt_label = f"Day {a_start} to Day {a_end}"
        asmt_dur = f"{a_days * 24} Hours ({a_days} Days)"
        
    asmt_topic = {
        "day_label": asmt_label,
        "topic_name": "Mandatory Final Assessment Evaluation Window",
        "estimated_duration_hours": asmt_dur
    }

    # 2. Shadow Topic
    if sr_match:
        s_start, s_end = int(sr_match.group(1)), int(sr_match.group(2))
        sr_label = f"Day {s_start} to Day {s_end} (Shadow Phase)"
        s_days = max(1, s_end - s_start + 1)
        sr_dur = f"{s_days * 24} Hours ({s_days} Days / 2 Weeks)"
    else:
        s_days = 14
        s_start = a_end + 1
        s_end = s_start + 13
        sr_label = f"Day {s_start} to Day {s_end} (Shadow Phase)"
        sr_dur = f"{s_days * 24} Hours ({s_days} Days / 2 Weeks)"
        
    sr_topic = {
        "day_label": sr_label,
        "topic_name": "Practical Shadow Experience & Hands-on Ticket Resolution",
        "estimated_duration_hours": sr_dur
    }

    # 3. Lead Topic
    if lr_match:
        l_start = int(lr_match.group(1))
        lr_label = f"Day {l_start} onwards (Lead Phase)"
    else:
        l_start = s_end + 1
        lr_label = f"Day {l_start} onwards (Lead Phase)"
        
    lr_topic = {
        "day_label": lr_label,
        "topic_name": "Independent Project Leadership & Transition Completion",
        "estimated_duration_hours": "Ongoing (Lead Phase)"
    }

    final_topics = ka_topics + [asmt_topic, sr_topic, lr_topic]
    return final_topics

def extract_and_save_topics(plan_id, generated_content, default_asmt_days=3):
    topics = []
    try:
        extraction_prompt = load_prompt("plan_topic_extraction.txt", generated_content=generated_content)
        extraction_response = call_llm(extraction_prompt)
        
        import re
        clean_json = extraction_response.strip()
        match = re.search(r'\[.*\]', clean_json, re.DOTALL)
        if match:
            clean_json = match.group(0)
            topics = json.loads(clean_json)
    except Exception as e:
        logging.warning(f"LLM topic extraction notice for plan {plan_id}: {e}")

    # Count extracted KA topics from LLM
    ka_topics_count = sum(1 for t in topics if 'assessment' not in (str(t.get('day_label','')) + str(t.get('topic_name',''))).lower() and 'shadow' not in (str(t.get('day_label','')) + str(t.get('topic_name',''))).lower() and 'lead' not in (str(t.get('day_label','')) + str(t.get('topic_name',''))).lower())

    # Count total Day X headings in generated_content markdown
    markdown_day_count = len(re.findall(r'^Day\s+\d+:', generated_content, re.MULTILINE | re.IGNORECASE))

    # If LLM response was truncated or yielded too few topics compared to markdown days, use deterministic Python parser
    if ka_topics_count < markdown_day_count:
        topics = parse_topics_directly_from_content(generated_content, default_asmt_days=default_asmt_days)
    else:
        # Dynamically determine the maximum KA day (ka_last_day) from extracted topics
        ka_last_day = 1
        filtered_topics = []
        for t in topics:
            d_label = str(t.get('day_label', ''))
            t_name = str(t.get('topic_name', ''))
            full_str = (d_label + ' ' + t_name).lower()
            
            # Skip any literal "Timeline:" bullet items accidentally extracted as topic rows
            if t_name.strip().lower().startswith('timeline:') or t_name.strip().lower().startswith('• timeline:'):
                continue

            # Skip any LLM-hallucinated daily 2-hour topics for shadow/ticket resolution in post-KA phase
            if ('shadow' in full_str or 'ticket resolution' in full_str or 'lead' in full_str) and re.search(r'Day\s+(?:1[7-9]|[2-9]\d)', d_label, re.IGNORECASE):
                continue

            filtered_topics.append(t)
            if 'assessment' not in full_str and 'shadow' not in full_str and 'lead' not in full_str:
                day_nums = re.findall(r'Day\s+(\d+)', d_label, re.IGNORECASE)
                for dn in day_nums:
                    ka_last_day = max(ka_last_day, int(dn))

        topics = filtered_topics

    # Reconstruct Post-KA topics strictly in order: Assessment -> Shadow -> Lead, matching markdown dates
    topics = ensure_post_ka_phase_order(topics, generated_content, ka_last_day if 'ka_last_day' in locals() else 1, default_asmt_days=default_asmt_days)

    # Clear existing topics if this is a resync
    execute_write("DELETE FROM plan_topics WHERE plan_id = %s", (plan_id,))
    
    count = 0
    for item in topics:
        query = "INSERT INTO plan_topics (plan_id, day_label, topic_name, estimated_duration_hours) VALUES (%s, %s, %s, %s)"
        execute_write(query, (plan_id, item.get('day_label', 'General'), item.get('topic_name'), item.get('estimated_duration_hours', 'N/A')))
        count += 1
        
    return count

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

    # Check if uploaded document is already a complete day-wise KT plan
    has_day_sessions = bool(re.search(r'Day\s+\d+[:\s]', combined_text, re.IGNORECASE))
    has_plan_structure = bool(re.search(r'(Sessions\s*/\s*Topics\s*Breakdown|Knowledge\s*Acquisition\s*\(KA\)\s*Phase|Final\s*Assessment\s*Evaluation\s*Window|Shadow\s*Resourcing)', combined_text, re.IGNORECASE))
    is_full_plan = has_day_sessions and (has_plan_structure or 'daily total' in combined_text.lower())
    
    clean_plan_content = None
    if is_full_plan:
        clean_plan_content = combined_text
        clean_plan_content = re.sub(r'---\s*Document:[^\n]+\n?', '', clean_plan_content).strip()
        
        # If extracted_app_name is generic or filename, try to find official plan title from text
        title_match = re.search(r'Tentative\s+(?:KT|Reverse-KT)\s+Plan[:\s-]+([^\n\r]+)', combined_text, re.IGNORECASE)
        if title_match:
            extracted_app_name = clean_name(title_match.group(1).strip())
        else:
            header_match = re.search(r'Track\s*/\s*Application[:\s-]+([^\n\r\|]+)', combined_text, re.IGNORECASE)
            if header_match:
                extracted_app_name = clean_name(header_match.group(1).strip())

    return {
        "application_name": extracted_app_name or "Hospital Management System",
        "scope_description": extracted_scope,
        "is_full_plan": is_full_plan,
        "plan_content": clean_plan_content
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

        # 1. Dynamically determine ka_last_day from DB plan_topics (checking KA rows ONLY)
        ka_last_day = 1
        ka_rows = execute_query(
            "SELECT day_label, topic_name FROM plan_topics WHERE plan_id = %s",
            (plan_id,)
        )
        if ka_rows:
            for r in ka_rows:
                lbl = str(r.get('day_label', ''))
                tname = str(r.get('topic_name', ''))
                full_str = (lbl + ' ' + tname).lower()
                if 'assessment' in full_str or 'shadow' in full_str or 'lead' in full_str:
                    continue
                matches = re.findall(r'Day\s+(\d+)', lbl, re.IGNORECASE)
                for m in matches:
                    ka_last_day = max(ka_last_day, int(m))

        # Fallback to ka_content_part in markdown if plan_topics yielded nothing
        if ka_last_day == 1 and content:
            ka_content_part = re.split(r'####?\s*Final\s*Assessment', content, flags=re.IGNORECASE)[0]
            content_day_matches = re.findall(r'Day\s+(\d+)', ka_content_part, re.IGNORECASE)
            if content_day_matches:
                ka_last_day = max(int(m) for m in content_day_matches)

        asmt_start = ka_last_day + 1
        asmt_end = ka_last_day + new_assessment_days
        sr_start = asmt_end + 1
        sr_end = sr_start + 13 # 14 days
        lr_start = sr_end + 1

        # 2. Reconstruct the 3 Post-KA Phase blocks in Markdown text cleanly
        content = sanitize_and_resolve_timeline(content, new_assessment_days)

        # Write updated content and project_config back to kt_plans table
        execute_write(
            "UPDATE kt_plans SET generated_content = %s, project_config = %s, final_deadline_extension_days = %s WHERE id = %s",
            (content, json.dumps(proj_config), new_assessment_days, plan_id)
        )

        # 3. Super-Safe DB Update for plan_topics table:
        # KA Phase rows in plan_topics table are 100% UNTOUCHED!
        # Delete ONLY the 3 Post-KA phase rows and re-insert updated Post-KA rows cleanly.
        execute_write(
            """DELETE FROM plan_topics 
               WHERE plan_id = %s 
               AND (LOWER(topic_name) LIKE %s 
                    OR LOWER(topic_name) LIKE %s 
                    OR LOWER(topic_name) LIKE %s
                    OR LOWER(day_label) LIKE %s
                    OR LOWER(day_label) LIKE %s)""",
            (plan_id, '%assessment%', '%shadow%', '%lead%', '%shadow%', '%lead%')
        )

        asmt_hrs = new_assessment_days * 24
        asmt_label = f"Day {asmt_start} to Day {asmt_end}"
        asmt_dur = f"{asmt_hrs} Hours ({new_assessment_days} Days)"

        sr_days = 14
        sr_hrs = sr_days * 24
        sr_label = f"Day {sr_start} to Day {sr_end} (Shadow Phase)"
        sr_dur = f"{sr_hrs} Hours ({sr_days} Days / 2 Weeks)"

        lr_label = f"Day {lr_start} onwards (Lead Phase)"

        execute_write(
            "INSERT INTO plan_topics (plan_id, day_label, topic_name, estimated_duration_hours) VALUES (%s, %s, %s, %s)",
            (plan_id, asmt_label, "Mandatory Final Assessment Evaluation Window", asmt_dur)
        )
        execute_write(
            "INSERT INTO plan_topics (plan_id, day_label, topic_name, estimated_duration_hours) VALUES (%s, %s, %s, %s)",
            (plan_id, sr_label, "Practical Shadow Experience & Hands-on Ticket Resolution", sr_dur)
        )
        execute_write(
            "INSERT INTO plan_topics (plan_id, day_label, topic_name, estimated_duration_hours) VALUES (%s, %s, %s, %s)",
            (plan_id, lr_label, "Independent Project Leadership & Transition Completion", "Ongoing (Lead Phase)")
        )

    except Exception as e:
        logging.error(f"Error in recalculate_plan_timeline_service: {e}")
