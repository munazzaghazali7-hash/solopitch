-- SoloPitch database schema
-- Run this once against your PostgreSQL instance before starting the agent.

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- Leads / clients table
CREATE TABLE IF NOT EXISTS leads (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name              TEXT NOT NULL,
    company           TEXT,
    industry          TEXT,
    email             TEXT,
    last_contacted_at TIMESTAMP,
    status            TEXT DEFAULT 'cold'
        CHECK (status IN ('cold', 'warm', 'active', 'converted', 'disqualified'))
);

-- Outreach log — one row per pitch sent
CREATE TABLE IF NOT EXISTS outreach_log (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    lead_id           UUID REFERENCES leads(id) ON DELETE CASCADE,
    pitch_draft       TEXT,
    sent_at           TIMESTAMP DEFAULT NOW(),
    response_received BOOLEAN DEFAULT FALSE,
    meeting_booked    BOOLEAN DEFAULT FALSE
);

-- Sample seed data for demo
INSERT INTO leads (name, company, industry, email, last_contacted_at, status) VALUES
    ('Priya Sharma',   'FinTrack',      'fintech',     'priya@fintrack.io',      NOW() - INTERVAL '45 days', 'cold'),
    ('Arjun Mehta',    'HealthStack',   'healthtech',  'arjun@healthstack.in',   NOW() - INTERVAL '60 days', 'cold'),
    ('Sneha Kapoor',   'EduForward',    'edtech',      'sneha@eduforward.co',    NOW() - INTERVAL '35 days', 'cold'),
    ('Rahul Nair',     'LogiFlow',      'logistics',   'rahul@logiflow.in',      NOW() - INTERVAL '90 days', 'cold'),
    ('Divya Reddy',    'GreenMatter',   'cleantech',   'divya@greenmatter.io',   NULL,                       'cold'),
    ('Vikram Singh',   'CloudSuite',    'saas',        'vikram@cloudsuite.com',  NOW() - INTERVAL '10 days', 'warm');
-- Note: Vikram was contacted recently so he won't appear in cold lead queries.
