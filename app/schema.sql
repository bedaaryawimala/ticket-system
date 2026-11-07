CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    email TEXT NOT NULL COLLATE NOCASE UNIQUE,
    display_name TEXT NOT NULL CHECK (length(trim(display_name)) BETWEEN 1 AND 80),
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('requester', 'agent', 'admin')),
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    session_version INTEGER NOT NULL DEFAULT 0 CHECK (session_version >= 0),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS password_reset_tokens (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    used_at TEXT
);

CREATE TABLE IF NOT EXISTS tickets (
    id INTEGER PRIMARY KEY,
    requester_id INTEGER NOT NULL REFERENCES users(id),
    assigned_agent_id INTEGER REFERENCES users(id),
    title TEXT NOT NULL CHECK (length(trim(title)) BETWEEN 1 AND 120),
    description TEXT NOT NULL CHECK (length(trim(description)) BETWEEN 1 AND 5000),
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'in_progress', 'resolved', 'closed')),
    urgency TEXT NOT NULL DEFAULT 'normal' CHECK (urgency IN ('low', 'normal', 'high')),
    work_blocked INTEGER CHECK (work_blocked IN (0, 1)),
    workaround_available INTEGER CHECK (workaround_available IN (0, 1)),
    waiting_impact TEXT CHECK (waiting_impact IN ('minor', 'disruptive', 'serious')),
    version INTEGER NOT NULL DEFAULT 0 CHECK (version >= 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    resolved_at TEXT,
    closed_at TEXT,
    CHECK (status != 'in_progress' OR assigned_agent_id IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS comments (
    id INTEGER PRIMARY KEY,
    ticket_id INTEGER NOT NULL REFERENCES tickets(id),
    author_id INTEGER NOT NULL REFERENCES users(id),
    body TEXT NOT NULL CHECK (length(trim(body)) BETWEEN 1 AND 2000),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ticket_events (
    id INTEGER PRIMARY KEY,
    ticket_id INTEGER NOT NULL REFERENCES tickets(id),
    actor_id INTEGER REFERENCES users(id),
    event_type TEXT NOT NULL CHECK (event_type IN ('created', 'claimed', 'assigned', 'reassigned', 'unassigned', 'started', 'resolved', 'closed', 'reopened', 'commented', 'urgency_changed')),
    from_status TEXT,
    to_status TEXT,
    from_urgency TEXT CHECK (from_urgency IN ('low', 'normal', 'high')),
    to_urgency TEXT CHECK (to_urgency IN ('low', 'normal', 'high')),
    from_assignee_id INTEGER REFERENCES users(id),
    to_assignee_id INTEGER REFERENCES users(id),
    comment_id INTEGER REFERENCES comments(id),
    reason TEXT CHECK (reason IS NULL OR length(trim(reason)) BETWEEN 1 AND 500),
    occurred_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS error_events (
    id TEXT PRIMARY KEY,
    occurred_at TEXT NOT NULL,
    severity TEXT NOT NULL CHECK (severity IN ('error', 'critical')),
    code TEXT NOT NULL,
    component TEXT NOT NULL,
    safe_message TEXT NOT NULL,
    route_template TEXT,
    http_status INTEGER NOT NULL,
    request_id TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS error_reviews (
    error_id TEXT PRIMARY KEY REFERENCES error_events(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'new' CHECK (status IN ('new', 'reviewed')),
    reviewed_by INTEGER REFERENCES users(id),
    reviewed_at TEXT,
    CHECK ((status = 'new' AND reviewed_by IS NULL AND reviewed_at IS NULL)
        OR (status = 'reviewed' AND reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS error_review_notes (
    id INTEGER PRIMARY KEY,
    error_id TEXT NOT NULL REFERENCES error_events(id) ON DELETE CASCADE,
    admin_id INTEGER NOT NULL REFERENCES users(id),
    body TEXT NOT NULL CHECK (length(trim(body)) BETWEEN 1 AND 2000),
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tickets_requester ON tickets(requester_id, created_at);
CREATE INDEX IF NOT EXISTS idx_password_resets_user ON password_reset_tokens(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_tickets_agent ON tickets(assigned_agent_id, status);
CREATE INDEX IF NOT EXISTS idx_tickets_queue ON tickets(status, created_at);
CREATE INDEX IF NOT EXISTS idx_comments_ticket ON comments(ticket_id, created_at);
CREATE INDEX IF NOT EXISTS idx_ticket_events_ticket ON ticket_events(ticket_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_errors_time ON error_events(occurred_at);
CREATE INDEX IF NOT EXISTS idx_errors_code ON error_events(code, severity);
CREATE INDEX IF NOT EXISTS idx_error_reviews_status ON error_reviews(status);
