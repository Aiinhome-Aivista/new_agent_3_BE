import db

db.execute_write('''ALTER TABLE risks MODIFY COLUMN status ENUM('open', 'escalated', 'resolved', 'in_progress', 'solved') DEFAULT 'open';''')

db.execute_write('''
CREATE TABLE IF NOT EXISTS risk_assignments (
    id INT AUTO_INCREMENT PRIMARY KEY,
    risk_id INT NOT NULL,
    stakeholder_id INT NOT NULL,
    assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (risk_id) REFERENCES risks(id) ON DELETE CASCADE,
    FOREIGN KEY (stakeholder_id) REFERENCES stakeholders(id) ON DELETE CASCADE,
    UNIQUE KEY idx_risk_stakeholder (risk_id, stakeholder_id)
);
''')

db.execute_write('''
CREATE TABLE IF NOT EXISTS risk_comments (
    id INT AUTO_INCREMENT PRIMARY KEY,
    risk_id INT NOT NULL,
    stakeholder_id INT NOT NULL,
    comment_text TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (risk_id) REFERENCES risks(id) ON DELETE CASCADE,
    FOREIGN KEY (stakeholder_id) REFERENCES stakeholders(id) ON DELETE CASCADE
);
''')

print('DB Setup complete')
