-- schema.sql
CREATE TABLE IF NOT EXISTS stakeholders (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    email VARCHAR(255) NOT NULL UNIQUE,
    role ENUM('engagement_manager','outgoing_sme','incoming_member','leadership', 'manager') NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS kt_plans (
    id INT AUTO_INCREMENT PRIMARY KEY,
    application_name VARCHAR(255) NOT NULL,
    scope_description TEXT,
    plan_type ENUM('KT','Reverse-KT') NOT NULL,
    generated_content TEXT,
    status ENUM('draft','approved') DEFAULT 'draft',
    created_by INT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (created_by) REFERENCES stakeholders(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS meetings (
    id INT AUTO_INCREMENT PRIMARY KEY,
    plan_id INT NOT NULL,
    title VARCHAR(255) NOT NULL,
    scheduled_at DATETIME NOT NULL,
    organizer_id INT,
    description TEXT,
    meeting_link VARCHAR(500),
    status ENUM('scheduled','completed','cancelled') DEFAULT 'scheduled',
    FOREIGN KEY (plan_id) REFERENCES kt_plans(id) ON DELETE CASCADE,
    FOREIGN KEY (organizer_id) REFERENCES users(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS attendance (
    id INT AUTO_INCREMENT PRIMARY KEY,
    meeting_id INT NOT NULL,
    stakeholder_id INT NOT NULL,
    attended BOOLEAN DEFAULT FALSE,
    notes TEXT,
    FOREIGN KEY (meeting_id) REFERENCES meetings(id) ON DELETE CASCADE,
    FOREIGN KEY (stakeholder_id) REFERENCES stakeholders(id) ON DELETE CASCADE,
    UNIQUE(meeting_id, stakeholder_id)
);

CREATE TABLE IF NOT EXISTS completion_tracking (
    id INT AUTO_INCREMENT PRIMARY KEY,
    plan_id INT NOT NULL,
    topic VARCHAR(255) NOT NULL,
    completion_percent INT DEFAULT 0,
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (plan_id) REFERENCES kt_plans(id) ON DELETE CASCADE,
    UNIQUE(plan_id, topic)
);

CREATE TABLE IF NOT EXISTS risks (
    id INT AUTO_INCREMENT PRIMARY KEY,
    plan_id INT NOT NULL,
    description TEXT NOT NULL,
    severity ENUM('low','medium','high','critical') NOT NULL,
    status ENUM('open','escalated','resolved') DEFAULT 'open',
    detected_by ENUM('ai','manual') NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (plan_id) REFERENCES kt_plans(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS assessments (
    id INT AUTO_INCREMENT PRIMARY KEY,
    plan_id INT NOT NULL,
    stakeholder_id INT NOT NULL,
    question TEXT NOT NULL,
    answer TEXT,
    ai_score INT,
    feedback TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (plan_id) REFERENCES kt_plans(id) ON DELETE CASCADE,
    FOREIGN KEY (stakeholder_id) REFERENCES stakeholders(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS reports (
    id INT AUTO_INCREMENT PRIMARY KEY,
    plan_id INT NOT NULL,
    report_type ENUM('weekly','final') NOT NULL,
    file_path VARCHAR(500) NOT NULL,
    generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (plan_id) REFERENCES kt_plans(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS chat_history (
    id INT AUTO_INCREMENT PRIMARY KEY,
    session_id VARCHAR(255) NOT NULL,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS users (
    id INT AUTO_INCREMENT PRIMARY KEY,
    full_name VARCHAR(255) NOT NULL,
    email VARCHAR(255) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    role VARCHAR(255) NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS knowledge_documents (
    id INT AUTO_INCREMENT PRIMARY KEY,
    plan_id INT NOT NULL,
    filename VARCHAR(255) NOT NULL,
    chunk_count INT NOT NULL,
    uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (plan_id) REFERENCES kt_plans(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS guardrail_logs (
    id INT AUTO_INCREMENT PRIMARY KEY,
    rail_type VARCHAR(50) NOT NULL,
    passed BOOLEAN NOT NULL,
    reason TEXT,
    endpoint VARCHAR(100),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
