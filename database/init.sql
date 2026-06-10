-- Legal AI Agent — Database Schema
-- Extracted from production Supabase DB
-- Auto-generated for Docker deployment

-- ============================================================
-- EXTENSIONS
-- ============================================================

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "vector";
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ============================================================
-- CUSTOM TYPES (ENUMs)
-- ============================================================

CREATE TYPE agent_type AS ENUM ('qa', 'review', 'compliance', 'draft', 'research', 'general', 'batch');
CREATE TYPE doc_status AS ENUM ('uploaded', 'processing', 'analyzed', 'error');
CREATE TYPE doc_type AS ENUM ('hop_dong_lao_dong', 'hop_dong_thuong_mai', 'hop_dong_dich_vu', 'noi_quy', 'quy_che', 'quyet_dinh', 'cong_van', 'bien_ban', 'bao_cao', 'phu_luc', 'other');
CREATE TYPE law_status AS ENUM ('active', 'expired', 'amended', 'repealed', 'pending');
CREATE TYPE law_type AS ENUM ('hien_phap', 'bo_luat', 'luat', 'nghi_dinh', 'thong_tu', 'quyet_dinh', 'nghi_quyet', 'cong_van', 'other');
CREATE TYPE legal_domain AS ENUM ('lao_dong', 'doanh_nghiep', 'dan_su', 'thuong_mai', 'thue', 'dat_dai', 'dau_tu', 'bhxh', 'atvs_ld', 'so_huu_tri_tue', 'hinh_su', 'other');
CREATE TYPE message_role AS ENUM ('user', 'assistant', 'system');
CREATE TYPE plan_type AS ENUM ('trial', 'starter', 'pro', 'enterprise');
CREATE TYPE user_role AS ENUM ('owner', 'admin', 'member', 'viewer', 'superadmin');

-- ============================================================
-- TABLES
-- ============================================================

-- Table: announcements
CREATE TABLE IF NOT EXISTS announcements (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    title VARCHAR(500),
    content TEXT,
    author_id UUID,
    target VARCHAR(50) DEFAULT 'all'::character varying,
    created_at TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (id)
);

-- Table: api_keys
CREATE TABLE IF NOT EXISTS api_keys (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    company_id UUID NOT NULL,
    name TEXT NOT NULL,
    key_hash TEXT NOT NULL,
    key_prefix TEXT NOT NULL,
    permissions TEXT[] DEFAULT ARRAY['read'::text, 'ask'::text, 'review'::text],
    rate_limit INTEGER DEFAULT 60,
    is_active BOOLEAN DEFAULT true,
    last_used_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (id)
);

-- Table: audit_logs
CREATE TABLE IF NOT EXISTS audit_logs (
    id SERIAL NOT NULL,
    company_id UUID,
    user_id UUID,
    action VARCHAR(50) NOT NULL,
    resource_type VARCHAR(50),
    resource_id VARCHAR(100),
    details JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (id)
);

-- Table: chat_sessions
CREATE TABLE IF NOT EXISTS chat_sessions (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    company_id UUID NOT NULL,
    user_id UUID,
    title TEXT,
    agent_type agent_type DEFAULT 'qa'::agent_type,
    status TEXT DEFAULT 'active'::text,
    metadata JSONB DEFAULT '{}'::jsonb,
    message_count INTEGER DEFAULT 0,
    last_message_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (id)
);

-- Table: companies
CREATE TABLE IF NOT EXISTS companies (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    name TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    plan plan_type DEFAULT 'trial'::plan_type,
    monthly_quota INTEGER DEFAULT 50,
    used_quota INTEGER DEFAULT 0,
    quota_reset_at TIMESTAMPTZ DEFAULT (date_trunc('month'::text, now()) + '1 mon'::interval),
    settings JSONB DEFAULT '{}'::jsonb,
    industry TEXT,
    employee_count INTEGER,
    tax_code TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    billing_email TEXT,
    billing_address TEXT,
    payment_method TEXT,
    subscription_id TEXT,
    subscription_status TEXT DEFAULT 'active'::text,
    trial_ends_at TIMESTAMPTZ,
    metadata JSONB DEFAULT '{}'::jsonb,
    is_active BOOLEAN DEFAULT true,
    PRIMARY KEY (id)
);

-- Table: company_chunks
CREATE TABLE IF NOT EXISTS company_chunks (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    company_id UUID NOT NULL,
    document_id UUID,
    content TEXT NOT NULL,
    embedding vector,
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (id)
);

-- Table: company_invites
CREATE TABLE IF NOT EXISTS company_invites (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    company_id UUID NOT NULL,
    inviter_id UUID,
    email TEXT NOT NULL,
    role user_role DEFAULT 'member'::user_role,
    token TEXT NOT NULL,
    expires_at TIMESTAMPTZ DEFAULT (now() + '7 days'::interval),
    accepted_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (id)
);

-- Table: contracts
CREATE TABLE IF NOT EXISTS contracts (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    company_id UUID NOT NULL,
    uploaded_by UUID,
    name VARCHAR(500) NOT NULL,
    contract_type VARCHAR(100),
    parties JSONB DEFAULT '[]'::jsonb,
    start_date DATE,
    end_date DATE,
    file_path TEXT,
    file_type VARCHAR(50),
    extracted_text TEXT,
    status VARCHAR(50) DEFAULT 'active'::character varying,
    review_result JSONB,
    notes TEXT,
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    folder_id UUID,
    deleted_at TIMESTAMPTZ,
    tags TEXT[] DEFAULT '{}'::text[],
    content TEXT,
    PRIMARY KEY (id)
);

-- Table: document_annotations
CREATE TABLE IF NOT EXISTS document_annotations (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    document_id UUID NOT NULL,
    company_id UUID NOT NULL,
    user_id UUID,
    text_selection TEXT,
    start_offset INTEGER,
    end_offset INTEGER,
    comment TEXT NOT NULL,
    annotation_type VARCHAR(50) DEFAULT 'comment'::character varying,
    is_ai_generated BOOLEAN DEFAULT false,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (id)
);

-- Table: document_edits
CREATE TABLE IF NOT EXISTS document_edits (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    document_id UUID,
    company_id UUID NOT NULL,
    user_id UUID,
    edit_type VARCHAR(50) NOT NULL,
    old_content TEXT,
    new_content TEXT,
    description TEXT,
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (id)
);

-- Table: document_templates
CREATE TABLE IF NOT EXISTS document_templates (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    template_id TEXT NOT NULL,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    description TEXT,
    version TEXT DEFAULT '1.0'::text,
    legal_basis TEXT[],
    variables JSONB DEFAULT '[]'::jsonb NOT NULL,
    sections JSONB DEFAULT '[]'::jsonb NOT NULL,
    compliance_rules JSONB DEFAULT '[]'::jsonb,
    sample_output TEXT,
    is_active BOOLEAN DEFAULT true,
    usage_count INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (id)
);

-- Table: documents
CREATE TABLE IF NOT EXISTS documents (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    company_id UUID NOT NULL,
    uploaded_by UUID,
    name TEXT NOT NULL,
    file_path TEXT NOT NULL,
    file_size INTEGER,
    mime_type TEXT,
    doc_type doc_type,
    status doc_status DEFAULT 'uploaded'::doc_status,
    extracted_text TEXT,
    page_count INTEGER,
    analysis JSONB,
    risk_score INTEGER,
    issues_count INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT now(),
    analyzed_at TIMESTAMPTZ,
    folder_id UUID,
    deleted_at TIMESTAMPTZ,
    tags TEXT[] DEFAULT '{}'::text[],
    PRIMARY KEY (id)
);

-- Table: folders
CREATE TABLE IF NOT EXISTS folders (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    company_id UUID NOT NULL,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    parent_id UUID,
    folder_type VARCHAR(50) DEFAULT 'general'::character varying,
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (id)
);

-- Table: generated_documents
CREATE TABLE IF NOT EXISTS generated_documents (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    company_id UUID NOT NULL,
    template_id TEXT,
    user_id UUID,
    name TEXT NOT NULL,
    variables JSONB DEFAULT '{}'::jsonb,
    content TEXT,
    file_path TEXT,
    format TEXT DEFAULT 'docx'::text,
    status TEXT DEFAULT 'generated'::text,
    batch_id UUID,
    created_at TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (id)
);

-- Table: law_chunks
CREATE TABLE IF NOT EXISTS law_chunks (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    law_id UUID NOT NULL,
    chapter TEXT,
    section TEXT,
    article TEXT,
    clause TEXT,
    point TEXT,
    title TEXT,
    content TEXT NOT NULL,
    parent_context TEXT,
    embedding vector,
    domains legal_domain[],
    keywords TEXT[],
    created_at TIMESTAMPTZ DEFAULT now(),
    tsv TSVECTOR,
    PRIMARY KEY (id)
);

-- Table: law_documents
CREATE TABLE IF NOT EXISTS law_documents (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    title TEXT NOT NULL,
    law_number TEXT NOT NULL,
    law_type law_type NOT NULL,
    issuer TEXT NOT NULL,
    signer TEXT,
    issued_date DATE,
    effective_date DATE,
    expiry_date DATE,
    status law_status DEFAULT 'active'::law_status,
    domains legal_domain[] NOT NULL,
    replaces TEXT[],
    amended_by TEXT[],
    full_text TEXT,
    summary TEXT,
    table_of_contents JSONB,
    source_url TEXT,
    source_site TEXT,
    article_count INTEGER,
    word_count INTEGER,
    crawled_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    tsv TSVECTOR,
    PRIMARY KEY (id)
);

-- Table: law_relations
CREATE TABLE IF NOT EXISTS law_relations (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    source_law_id UUID,
    source_article TEXT,
    target_law_id UUID,
    target_article TEXT,
    relation_type TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (id)
);

-- Table: llm_usage
CREATE TABLE IF NOT EXISTS llm_usage (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    company_id UUID,
    user_id UUID,
    provider VARCHAR(50) NOT NULL,
    model VARCHAR(100) NOT NULL,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    estimated_cost NUMERIC DEFAULT 0,
    request_type VARCHAR(50),
    created_at TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (id)
);

-- Table: messages
CREATE TABLE IF NOT EXISTS messages (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    session_id UUID NOT NULL,
    company_id UUID NOT NULL,
    role message_role NOT NULL,
    content TEXT NOT NULL,
    citations JSONB DEFAULT '[]'::jsonb,
    confidence DOUBLE PRECISION,
    tokens_used INTEGER,
    model TEXT,
    feedback TEXT,
    feedback_note TEXT,
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (id)
);

-- Table: oauth_states
CREATE TABLE IF NOT EXISTS oauth_states (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    company_id UUID NOT NULL,
    provider VARCHAR(50) NOT NULL,
    state_token VARCHAR(255) NOT NULL,
    redirect_uri TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    expires_at TIMESTAMPTZ DEFAULT (now() + '00:10:00'::interval),
    PRIMARY KEY (id)
);

-- Table: platform_logs
CREATE TABLE IF NOT EXISTS platform_logs (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    company_id UUID,
    user_id UUID,
    endpoint VARCHAR(200),
    method VARCHAR(10),
    status_code INTEGER,
    response_time_ms INTEGER,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    ip_address VARCHAR(50),
    created_at TIMESTAMPTZ DEFAULT now(),
    action VARCHAR(100),
    target_type VARCHAR(50),
    target_id VARCHAR(100),
    details JSONB DEFAULT '{}'::jsonb,
    PRIMARY KEY (id)
);

-- Table: platform_settings
CREATE TABLE IF NOT EXISTS platform_settings (
    key VARCHAR(100) NOT NULL,
    value JSONB NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT now(),
    updated_by UUID,
    PRIMARY KEY (key)
);

-- Table: usage_logs
CREATE TABLE IF NOT EXISTS usage_logs (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    company_id UUID NOT NULL,
    user_id UUID,
    api_key_id UUID,
    endpoint TEXT NOT NULL,
    agent_type agent_type,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    embedding_tokens INTEGER DEFAULT 0,
    total_cost_usd NUMERIC DEFAULT 0,
    latency_ms INTEGER,
    status_code INTEGER,
    request_metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (id)
);

-- Table: users
CREATE TABLE IF NOT EXISTS users (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    company_id UUID,
    role user_role DEFAULT 'member'::user_role,
    full_name TEXT,
    email TEXT UNIQUE,
    avatar_url TEXT,
    preferences JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT now(),
    auth_id UUID,
    password_hash TEXT,
    user_settings JSONB DEFAULT '{}'::jsonb,
    last_login_at TIMESTAMPTZ,
    is_active BOOLEAN DEFAULT true,
    PRIMARY KEY (id)
);

-- ============================================================
-- INDEXES (Critical for performance)
-- ============================================================

-- Companies
CREATE INDEX IF NOT EXISTS idx_companies_slug ON companies(slug);

-- Law documents & chunks
CREATE INDEX IF NOT EXISTS idx_law_documents_law_number ON law_documents(law_number);
CREATE INDEX IF NOT EXISTS idx_law_documents_domains ON law_documents USING GIN(domains);
CREATE INDEX IF NOT EXISTS idx_law_documents_tsv ON law_documents USING GIN(tsv);
CREATE INDEX IF NOT EXISTS idx_law_chunks_law_id ON law_chunks(law_id);
CREATE INDEX IF NOT EXISTS idx_law_chunks_tsv ON law_chunks USING GIN(tsv);

-- Vector similarity search (if using pgvector)
CREATE INDEX IF NOT EXISTS idx_law_chunks_embedding ON law_chunks USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
CREATE INDEX IF NOT EXISTS idx_company_chunks_embedding ON company_chunks USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

-- Chat sessions & messages
CREATE INDEX IF NOT EXISTS idx_chat_sessions_company_id ON chat_sessions(company_id);
CREATE INDEX IF NOT EXISTS idx_messages_session_id ON messages(session_id);
CREATE INDEX IF NOT EXISTS idx_messages_company_id ON messages(company_id);

-- Documents
CREATE INDEX IF NOT EXISTS idx_documents_company_id ON documents(company_id);
CREATE INDEX IF NOT EXISTS idx_documents_folder_id ON documents(folder_id);

-- API keys
CREATE INDEX IF NOT EXISTS idx_api_keys_company_id ON api_keys(company_id);
CREATE INDEX IF NOT EXISTS idx_api_keys_key_hash ON api_keys(key_hash);

-- Users
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
CREATE INDEX IF NOT EXISTS idx_users_company_id ON users(company_id);

-- Usage logs (for analytics)
CREATE INDEX IF NOT EXISTS idx_usage_logs_company_id ON usage_logs(company_id);
CREATE INDEX IF NOT EXISTS idx_usage_logs_created_at ON usage_logs(created_at);

-- ============================================================
-- SEED DATA (Default templates, settings, etc.)
-- ============================================================

-- Insert default document templates (if not exists)
INSERT INTO document_templates (template_id, name, category, description, variables, sections)
VALUES 
    ('hop-dong-lao-dong-01', 'Hợp đồng lao động cơ bản', 'hop_dong_lao_dong', 'Mẫu hợp đồng lao động theo Bộ luật Lao động 2019', '[]'::jsonb, '[]'::jsonb),
    ('noi-quy-lao-dong-01', 'Nội quy lao động mẫu', 'noi_quy', 'Nội quy lao động cho doanh nghiệp', '[]'::jsonb, '[]'::jsonb)
ON CONFLICT DO NOTHING;

-- Insert default platform settings
INSERT INTO platform_settings (key, value)
VALUES 
    ('maintenance_mode', '"false"'::jsonb),
    ('default_plan_quota', '{"trial": 50, "starter": 200, "pro": 1000, "enterprise": 10000}'::jsonb),
    ('ai_models', '{"default": "gpt-4o-mini", "available": ["gpt-4o", "gpt-4o-mini", "claude-3-5-sonnet"]}'::jsonb)
ON CONFLICT (key) DO NOTHING;

-- ============================================================
-- NOTES
-- ============================================================

-- ⚠️  law_documents and law_chunks tables are EMPTY by default
-- 📊 Production DB has ~60K law_documents and ~117K law_chunks
-- 🚀 To populate: Run the legal crawler or import from backup
-- 🔍 Vector indexes (ivfflat) require data to train - will be created after inserts

-- ============================================================
-- END OF SCHEMA
-- ============================================================
