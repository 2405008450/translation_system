--
-- PostgreSQL database dump
--

\restrict EEsFTQiNWBGYttpLr5fdHkdpD4kMjrcaM1YnKte41Dj8jAVbPc5jXihy5uq9zqL

-- Dumped from database version 17.7
-- Dumped by pg_dump version 17.7

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: public; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA public;


--
-- Name: SCHEMA public; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON SCHEMA public IS 'standard public schema';


--
-- Name: update_reference_profiles_updated_at(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.update_reference_profiles_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;


--
-- Name: update_segment_comments_updated_at(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.update_segment_comments_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;


--
-- Name: update_updated_at_column(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.update_updated_at_column() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: assignment_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.assignment_events (
    id uuid DEFAULT (((((((((((lpad(to_hex((floor((random() * ('4294967296'::bigint)::double precision)))::bigint), 8, '0'::text) || '-'::text) || lpad(to_hex((floor((random() * (65536)::double precision)))::integer), 4, '0'::text)) || '-'::text) || '4'::text) || substr(lpad(to_hex((floor((random() * (4096)::double precision)))::integer), 3, '0'::text), 1, 3)) || '-'::text) || substr('89ab'::text, ((floor((random() * (4)::double precision)))::integer + 1), 1)) || substr(lpad(to_hex((floor((random() * (4096)::double precision)))::integer), 3, '0'::text), 1, 3)) || '-'::text) || lpad(to_hex((floor((random() * ('281474976710656'::bigint)::double precision)))::bigint), 12, '0'::text)))::uuid NOT NULL,
    project_id uuid NOT NULL,
    file_record_id uuid,
    assignee_id uuid NOT NULL,
    actor_id uuid,
    action character varying(40) NOT NULL,
    before_payload text DEFAULT '{}'::text NOT NULL,
    after_payload text DEFAULT '{}'::text NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: auto_tm_outbox; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.auto_tm_outbox (
    id uuid DEFAULT (((((((((((lpad(to_hex((floor((random() * ('4294967296'::bigint)::double precision)))::bigint), 8, '0'::text) || '-'::text) || lpad(to_hex((floor((random() * (65536)::double precision)))::integer), 4, '0'::text)) || '-'::text) || '4'::text) || substr(lpad(to_hex((floor((random() * (4096)::double precision)))::integer), 3, '0'::text), 1, 3)) || '-'::text) || substr('89ab'::text, ((floor((random() * (4)::double precision)))::integer + 1), 1)) || substr(lpad(to_hex((floor((random() * (4096)::double precision)))::integer), 3, '0'::text), 1, 3)) || '-'::text) || lpad(to_hex((floor((random() * ('281474976710656'::bigint)::double precision)))::bigint), 12, '0'::text)))::uuid NOT NULL,
    file_record_id uuid NOT NULL,
    segment_id uuid NOT NULL,
    sentence_id character varying(20) NOT NULL,
    collection_id uuid NOT NULL,
    source_text text NOT NULL,
    target_text text NOT NULL,
    source_language character varying(20) NOT NULL,
    target_language character varying(20) NOT NULL,
    creator_id uuid,
    status character varying(20) DEFAULT 'pending'::character varying NOT NULL,
    attempt_count integer DEFAULT 0 NOT NULL,
    error_message text DEFAULT ''::text NOT NULL,
    last_enqueued_at timestamp without time zone DEFAULT now() NOT NULL,
    processed_at timestamp without time zone,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: auto_tm_rematch_queue; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.auto_tm_rematch_queue (
    id uuid DEFAULT (((((((((((lpad(to_hex((floor((random() * ('4294967296'::bigint)::double precision)))::bigint), 8, '0'::text) || '-'::text) || lpad(to_hex((floor((random() * (65536)::double precision)))::integer), 4, '0'::text)) || '-'::text) || '4'::text) || substr(lpad(to_hex((floor((random() * (4096)::double precision)))::integer), 3, '0'::text), 1, 3)) || '-'::text) || substr('89ab'::text, ((floor((random() * (4)::double precision)))::integer + 1), 1)) || substr(lpad(to_hex((floor((random() * (4096)::double precision)))::integer), 3, '0'::text), 1, 3)) || '-'::text) || lpad(to_hex((floor((random() * ('281474976710656'::bigint)::double precision)))::bigint), 12, '0'::text)))::uuid NOT NULL,
    file_record_id uuid NOT NULL,
    collection_id uuid NOT NULL,
    pending_entry_count integer DEFAULT 0 NOT NULL,
    status character varying(20) DEFAULT 'pending'::character varying NOT NULL,
    first_pending_at timestamp without time zone,
    last_pending_at timestamp without time zone,
    last_processed_at timestamp without time zone,
    error_message text DEFAULT ''::text NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: document_statistics_report_items; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.document_statistics_report_items (
    id uuid DEFAULT (((((((((((lpad(to_hex((floor((random() * ('4294967296'::bigint)::double precision)))::bigint), 8, '0'::text) || '-'::text) || lpad(to_hex((floor((random() * (65536)::double precision)))::integer), 4, '0'::text)) || '-'::text) || '4'::text) || substr(lpad(to_hex((floor((random() * (4096)::double precision)))::integer), 3, '0'::text), 1, 3)) || '-'::text) || substr('89ab'::text, ((floor((random() * (4)::double precision)))::integer + 1), 1)) || substr(lpad(to_hex((floor((random() * (4096)::double precision)))::integer), 3, '0'::text), 1, 3)) || '-'::text) || lpad(to_hex((floor((random() * ('281474976710656'::bigint)::double precision)))::bigint), 12, '0'::text)))::uuid NOT NULL,
    report_id uuid NOT NULL,
    project_id uuid NOT NULL,
    file_record_id uuid,
    file_name character varying(255) NOT NULL,
    source_language character varying(20),
    target_language character varying(20),
    file_size_bytes integer,
    statistics text DEFAULT '{}'::text NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: document_statistics_reports; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.document_statistics_reports (
    id uuid DEFAULT (((((((((((lpad(to_hex((floor((random() * ('4294967296'::bigint)::double precision)))::bigint), 8, '0'::text) || '-'::text) || lpad(to_hex((floor((random() * (65536)::double precision)))::integer), 4, '0'::text)) || '-'::text) || '4'::text) || substr(lpad(to_hex((floor((random() * (4096)::double precision)))::integer), 3, '0'::text), 1, 3)) || '-'::text) || substr('89ab'::text, ((floor((random() * (4)::double precision)))::integer + 1), 1)) || substr(lpad(to_hex((floor((random() * (4096)::double precision)))::integer), 3, '0'::text), 1, 3)) || '-'::text) || lpad(to_hex((floor((random() * ('281474976710656'::bigint)::double precision)))::bigint), 12, '0'::text)))::uuid NOT NULL,
    project_id uuid NOT NULL,
    created_by_id uuid,
    file_ids text DEFAULT '[]'::text NOT NULL,
    total_files integer DEFAULT 0 NOT NULL,
    available_files integer DEFAULT 0 NOT NULL,
    totals text DEFAULT '{}'::text NOT NULL,
    status character varying(20) DEFAULT 'completed'::character varying NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: file_assignments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.file_assignments (
    id uuid DEFAULT (((((((((((lpad(to_hex((floor((random() * ('4294967296'::bigint)::double precision)))::bigint), 8, '0'::text) || '-'::text) || lpad(to_hex((floor((random() * (65536)::double precision)))::integer), 4, '0'::text)) || '-'::text) || '4'::text) || substr(lpad(to_hex((floor((random() * (4096)::double precision)))::integer), 3, '0'::text), 1, 3)) || '-'::text) || substr('89ab'::text, ((floor((random() * (4)::double precision)))::integer + 1), 1)) || substr(lpad(to_hex((floor((random() * (4096)::double precision)))::integer), 3, '0'::text), 1, 3)) || '-'::text) || lpad(to_hex((floor((random() * ('281474976710656'::bigint)::double precision)))::bigint), 12, '0'::text)))::uuid NOT NULL,
    project_id uuid NOT NULL,
    file_record_id uuid NOT NULL,
    assignee_id uuid NOT NULL,
    assigned_by_id uuid,
    assigned_at timestamp without time zone DEFAULT now() NOT NULL,
    revoked_by_id uuid,
    revoked_at timestamp without time zone,
    status character varying(20) DEFAULT 'active'::character varying NOT NULL
);


--
-- Name: file_records; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.file_records (
    filename character varying(255) NOT NULL,
    file_hash character varying(64),
    status character varying(20) DEFAULT 'draft'::character varying NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL,
    id uuid DEFAULT (((((((((((lpad(to_hex((floor((random() * ('4294967296'::bigint)::double precision)))::bigint), 8, '0'::text) || '-'::text) || lpad(to_hex((floor((random() * (65536)::double precision)))::integer), 4, '0'::text)) || '-'::text) || '4'::text) || substr(lpad(to_hex((floor((random() * (4096)::double precision)))::integer), 3, '0'::text), 1, 3)) || '-'::text) || substr('89ab'::text, ((floor((random() * (4)::double precision)))::integer + 1), 1)) || substr(lpad(to_hex((floor((random() * (4096)::double precision)))::integer), 3, '0'::text), 1, 3)) || '-'::text) || lpad(to_hex((floor((random() * ('281474976710656'::bigint)::double precision)))::bigint), 12, '0'::text)))::uuid NOT NULL,
    source_language character varying(20),
    target_language character varying(20),
    creator_id uuid,
    deadline timestamp without time zone,
    access_level character varying(20) DEFAULT 'team'::character varying NOT NULL,
    collection_id uuid,
    term_base_id uuid,
    project_id uuid,
    document_parse_mode character varying(20) DEFAULT 'full'::character varying NOT NULL,
    document_parse_options text DEFAULT '{}'::text NOT NULL,
    collection_ids_json text DEFAULT '[]'::text NOT NULL,
    document_statistics text DEFAULT '{}'::text NOT NULL,
    active_operation character varying(40),
    active_operation_token character varying(64),
    active_operation_updated_at timestamp without time zone,
    active_operation_user_id uuid,
    term_base_ids text DEFAULT '[]'::text NOT NULL,
    assignee_id uuid,
    assigned_by_id uuid,
    assigned_at timestamp without time zone,
    term_base_write_ids text DEFAULT '[]'::text NOT NULL,
    qa_term_base_ids text DEFAULT '[]'::text NOT NULL,
    glossary_base_ids text DEFAULT '[]'::text NOT NULL,
    tm_match_threshold double precision DEFAULT 0.8 NOT NULL
);


--
-- Name: glossary_bases; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.glossary_bases (
    id uuid DEFAULT (((((((((((lpad(to_hex((floor((random() * ('4294967296'::bigint)::double precision)))::bigint), 8, '0'::text) || '-'::text) || lpad(to_hex((floor((random() * (65536)::double precision)))::integer), 4, '0'::text)) || '-'::text) || '4'::text) || substr(lpad(to_hex((floor((random() * (4096)::double precision)))::integer), 3, '0'::text), 1, 3)) || '-'::text) || substr('89ab'::text, ((floor((random() * (4)::double precision)))::integer + 1), 1)) || substr(lpad(to_hex((floor((random() * (4096)::double precision)))::integer), 3, '0'::text), 1, 3)) || '-'::text) || lpad(to_hex((floor((random() * ('281474976710656'::bigint)::double precision)))::bigint), 12, '0'::text)))::uuid NOT NULL,
    name character varying(120) NOT NULL,
    description text,
    source_language character varying(20) NOT NULL,
    target_language character varying(20) NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: glossary_entries; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.glossary_entries (
    id uuid DEFAULT (((((((((((lpad(to_hex((floor((random() * ('4294967296'::bigint)::double precision)))::bigint), 8, '0'::text) || '-'::text) || lpad(to_hex((floor((random() * (65536)::double precision)))::integer), 4, '0'::text)) || '-'::text) || '4'::text) || substr(lpad(to_hex((floor((random() * (4096)::double precision)))::integer), 3, '0'::text), 1, 3)) || '-'::text) || substr('89ab'::text, ((floor((random() * (4)::double precision)))::integer + 1), 1)) || substr(lpad(to_hex((floor((random() * (4096)::double precision)))::integer), 3, '0'::text), 1, 3)) || '-'::text) || lpad(to_hex((floor((random() * ('281474976710656'::bigint)::double precision)))::bigint), 12, '0'::text)))::uuid NOT NULL,
    glossary_base_id uuid NOT NULL,
    source_text text NOT NULL,
    target_text text NOT NULL,
    note text,
    source_normalized text,
    source_language character varying(20) NOT NULL,
    target_language character varying(20) NOT NULL,
    creator_id uuid,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: issue_markers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.issue_markers (
    id uuid DEFAULT (((((((((((lpad(to_hex((floor((random() * ('4294967296'::bigint)::double precision)))::bigint), 8, '0'::text) || '-'::text) || lpad(to_hex((floor((random() * (65536)::double precision)))::integer), 4, '0'::text)) || '-'::text) || '4'::text) || substr(lpad(to_hex((floor((random() * (4096)::double precision)))::integer), 3, '0'::text), 1, 3)) || '-'::text) || substr('89ab'::text, ((floor((random() * (4)::double precision)))::integer + 1), 1)) || substr(lpad(to_hex((floor((random() * (4096)::double precision)))::integer), 3, '0'::text), 1, 3)) || '-'::text) || lpad(to_hex((floor((random() * ('281474976710656'::bigint)::double precision)))::bigint), 12, '0'::text)))::uuid NOT NULL,
    project_id uuid NOT NULL,
    file_record_id uuid,
    title character varying(160) DEFAULT ''::character varying NOT NULL,
    description text NOT NULL,
    category character varying(30) DEFAULT 'other'::character varying NOT NULL,
    severity character varying(20) DEFAULT 'medium'::character varying NOT NULL,
    status character varying(20) DEFAULT 'open'::character varying NOT NULL,
    page_url text,
    user_agent text,
    reporter_id uuid,
    resolved_by_id uuid,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL,
    resolved_at timestamp without time zone
);


--
-- Name: memory_bases; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.memory_bases (
    id uuid DEFAULT (((((((((((lpad(to_hex((floor((random() * ('4294967296'::bigint)::double precision)))::bigint), 8, '0'::text) || '-'::text) || lpad(to_hex((floor((random() * (65536)::double precision)))::integer), 4, '0'::text)) || '-'::text) || '4'::text) || substr(lpad(to_hex((floor((random() * (4096)::double precision)))::integer), 3, '0'::text), 1, 3)) || '-'::text) || substr('89ab'::text, ((floor((random() * (4)::double precision)))::integer + 1), 1)) || substr(lpad(to_hex((floor((random() * (4096)::double precision)))::integer), 3, '0'::text), 1, 3)) || '-'::text) || lpad(to_hex((floor((random() * ('281474976710656'::bigint)::double precision)))::bigint), 12, '0'::text)))::uuid NOT NULL,
    name character varying(120) NOT NULL,
    description text,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL,
    source_language character varying(20),
    target_language character varying(20)
);


--
-- Name: memory_entries; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.memory_entries (
    source_text text NOT NULL,
    target_text text NOT NULL,
    source_hash character varying(64),
    source_normalized text,
    created_at timestamp without time zone DEFAULT now(),
    updated_at timestamp without time zone DEFAULT now(),
    id uuid DEFAULT (((((((((((lpad(to_hex((floor((random() * ('4294967296'::bigint)::double precision)))::bigint), 8, '0'::text) || '-'::text) || lpad(to_hex((floor((random() * (65536)::double precision)))::integer), 4, '0'::text)) || '-'::text) || '4'::text) || substr(lpad(to_hex((floor((random() * (4096)::double precision)))::integer), 3, '0'::text), 1, 3)) || '-'::text) || substr('89ab'::text, ((floor((random() * (4)::double precision)))::integer + 1), 1)) || substr(lpad(to_hex((floor((random() * (4096)::double precision)))::integer), 3, '0'::text), 1, 3)) || '-'::text) || lpad(to_hex((floor((random() * ('281474976710656'::bigint)::double precision)))::bigint), 12, '0'::text)))::uuid NOT NULL,
    collection_id uuid,
    source_language character varying(20),
    target_language character varying(20),
    creator_id uuid
);


--
-- Name: notifications; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.notifications (
    id uuid DEFAULT (((((((((((lpad(to_hex((floor((random() * ('4294967296'::bigint)::double precision)))::bigint), 8, '0'::text) || '-'::text) || lpad(to_hex((floor((random() * (65536)::double precision)))::integer), 4, '0'::text)) || '-'::text) || '4'::text) || substr(lpad(to_hex((floor((random() * (4096)::double precision)))::integer), 3, '0'::text), 1, 3)) || '-'::text) || substr('89ab'::text, ((floor((random() * (4)::double precision)))::integer + 1), 1)) || substr(lpad(to_hex((floor((random() * (4096)::double precision)))::integer), 3, '0'::text), 1, 3)) || '-'::text) || lpad(to_hex((floor((random() * ('281474976710656'::bigint)::double precision)))::bigint), 12, '0'::text)))::uuid NOT NULL,
    user_id uuid NOT NULL,
    type character varying(40) NOT NULL,
    title character varying(200) NOT NULL,
    body text DEFAULT ''::text NOT NULL,
    project_id uuid,
    file_record_id uuid,
    related_event_id uuid,
    read_at timestamp without time zone,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: project_assignments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.project_assignments (
    id uuid DEFAULT (((((((((((lpad(to_hex((floor((random() * ('4294967296'::bigint)::double precision)))::bigint), 8, '0'::text) || '-'::text) || lpad(to_hex((floor((random() * (65536)::double precision)))::integer), 4, '0'::text)) || '-'::text) || '4'::text) || substr(lpad(to_hex((floor((random() * (4096)::double precision)))::integer), 3, '0'::text), 1, 3)) || '-'::text) || substr('89ab'::text, ((floor((random() * (4)::double precision)))::integer + 1), 1)) || substr(lpad(to_hex((floor((random() * (4096)::double precision)))::integer), 3, '0'::text), 1, 3)) || '-'::text) || lpad(to_hex((floor((random() * ('281474976710656'::bigint)::double precision)))::bigint), 12, '0'::text)))::uuid NOT NULL,
    project_id uuid NOT NULL,
    assignee_id uuid NOT NULL,
    assigned_by_id uuid,
    assigned_at timestamp without time zone DEFAULT now() NOT NULL,
    revoked_by_id uuid,
    revoked_at timestamp without time zone,
    status character varying(20) DEFAULT 'active'::character varying NOT NULL
);


--
-- Name: projects; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.projects (
    id uuid DEFAULT (((((((((((lpad(to_hex((floor((random() * ('4294967296'::bigint)::double precision)))::bigint), 8, '0'::text) || '-'::text) || lpad(to_hex((floor((random() * (65536)::double precision)))::integer), 4, '0'::text)) || '-'::text) || '4'::text) || substr(lpad(to_hex((floor((random() * (4096)::double precision)))::integer), 3, '0'::text), 1, 3)) || '-'::text) || substr('89ab'::text, ((floor((random() * (4)::double precision)))::integer + 1), 1)) || substr(lpad(to_hex((floor((random() * (4096)::double precision)))::integer), 3, '0'::text), 1, 3)) || '-'::text) || lpad(to_hex((floor((random() * ('281474976710656'::bigint)::double precision)))::bigint), 12, '0'::text)))::uuid NOT NULL,
    name character varying(200) NOT NULL,
    status character varying(20) DEFAULT 'draft'::character varying NOT NULL,
    source_language character varying(20),
    target_language character varying(20),
    creator_id uuid,
    deadline timestamp without time zone,
    access_level character varying(20) DEFAULT 'team'::character varying NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL,
    document_parse_mode character varying(20) DEFAULT 'full'::character varying NOT NULL,
    translation_guidelines text DEFAULT ''::text NOT NULL
);


--
-- Name: reference_files; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.reference_files (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    profile_id uuid,
    filename character varying(500) NOT NULL,
    file_path character varying(1000) NOT NULL,
    file_type character varying(50),
    file_size integer,
    is_bilingual_source boolean DEFAULT false,
    is_bilingual_target boolean DEFAULT false,
    bilingual_pair_id uuid,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: TABLE reference_files; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.reference_files IS '参考文件记录，存储上传的参考文件元信息';


--
-- Name: reference_profiles; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.reference_profiles (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    file_record_id uuid,
    source_files text DEFAULT '[]'::text NOT NULL,
    terminology text DEFAULT '[]'::text NOT NULL,
    translation_memory text DEFAULT '[]'::text NOT NULL,
    style_guide text,
    analysis_report text,
    overall_confidence double precision DEFAULT 0.0,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL,
    match_result text
);


--
-- Name: TABLE reference_profiles; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.reference_profiles IS '参考文件分析结果，存储术语、TM、风格和分析报告';


--
-- Name: COLUMN reference_profiles.terminology; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.reference_profiles.terminology IS '提取的术语列表 JSON';


--
-- Name: COLUMN reference_profiles.translation_memory; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.reference_profiles.translation_memory IS '提取的翻译记忆句对 JSON';


--
-- Name: COLUMN reference_profiles.analysis_report; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.reference_profiles.analysis_report IS 'AI 深度分析报告 JSON';


--
-- Name: COLUMN reference_profiles.match_result; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.reference_profiles.match_result IS '匹配结果JSON，包含exact_matches、fuzzy_matches、term_matches';


--
-- Name: segment_comments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.segment_comments (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    file_record_id uuid NOT NULL,
    segment_id uuid,
    anchor_mode character varying(20) DEFAULT 'sentence'::character varying NOT NULL,
    range_start_offset integer,
    range_end_offset integer,
    anchor_text text,
    body text NOT NULL,
    author_id uuid NOT NULL,
    parent_id uuid,
    status character varying(20) DEFAULT 'open'::character varying NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL,
    resolved_at timestamp without time zone
);


--
-- Name: segment_history; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.segment_history (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    segment_id uuid NOT NULL,
    file_record_id uuid NOT NULL,
    sentence_id character varying(20) NOT NULL,
    source_text text NOT NULL,
    target_text text NOT NULL,
    status character varying(20) NOT NULL,
    source character varying(20) NOT NULL,
    confirm_type character varying(30),
    operator_id uuid,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: segment_revisions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.segment_revisions (
    id uuid DEFAULT (((((((((((lpad(to_hex((floor((random() * ('4294967296'::bigint)::double precision)))::bigint), 8, '0'::text) || '-'::text) || lpad(to_hex((floor((random() * (65536)::double precision)))::integer), 4, '0'::text)) || '-'::text) || '4'::text) || substr(lpad(to_hex((floor((random() * (4096)::double precision)))::integer), 3, '0'::text), 1, 3)) || '-'::text) || substr('89ab'::text, ((floor((random() * (4)::double precision)))::integer + 1), 1)) || substr(lpad(to_hex((floor((random() * (4096)::double precision)))::integer), 3, '0'::text), 1, 3)) || '-'::text) || lpad(to_hex((floor((random() * ('281474976710656'::bigint)::double precision)))::bigint), 12, '0'::text)))::uuid NOT NULL,
    file_record_id uuid NOT NULL,
    segment_id uuid NOT NULL,
    sentence_id character varying(20) NOT NULL,
    before_text text DEFAULT ''::text NOT NULL,
    after_text text DEFAULT ''::text NOT NULL,
    source character varying(20) DEFAULT 'manual'::character varying NOT NULL,
    status character varying(20) DEFAULT 'pending'::character varying NOT NULL,
    author_id uuid,
    resolved_by_id uuid,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    resolved_at timestamp without time zone
);


--
-- Name: segments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.segments (
    sentence_id character varying(20) NOT NULL,
    source_text text NOT NULL,
    display_text text NOT NULL,
    target_text text DEFAULT ''::text NOT NULL,
    status character varying(20) DEFAULT 'none'::character varying NOT NULL,
    score double precision DEFAULT 0.0 NOT NULL,
    matched_source_text text,
    source character varying(20) DEFAULT 'tm'::character varying NOT NULL,
    block_type character varying(20) DEFAULT 'paragraph'::character varying NOT NULL,
    block_index integer DEFAULT 0 NOT NULL,
    row_index integer,
    cell_index integer,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL,
    id uuid DEFAULT (((((((((((lpad(to_hex((floor((random() * ('4294967296'::bigint)::double precision)))::bigint), 8, '0'::text) || '-'::text) || lpad(to_hex((floor((random() * (65536)::double precision)))::integer), 4, '0'::text)) || '-'::text) || '4'::text) || substr(lpad(to_hex((floor((random() * (4096)::double precision)))::integer), 3, '0'::text), 1, 3)) || '-'::text) || substr('89ab'::text, ((floor((random() * (4)::double precision)))::integer + 1), 1)) || substr(lpad(to_hex((floor((random() * (4096)::double precision)))::integer), 3, '0'::text), 1, 3)) || '-'::text) || lpad(to_hex((floor((random() * ('281474976710656'::bigint)::double precision)))::bigint), 12, '0'::text)))::uuid NOT NULL,
    file_record_id uuid NOT NULL,
    matched_collection_name character varying(120),
    matched_creator_name character varying(100),
    matched_created_at timestamp without time zone,
    matched_updated_at timestamp without time zone,
    llm_provider character varying(40),
    llm_model character varying(200),
    target_html text,
    source_word_count integer DEFAULT 0 NOT NULL,
    version integer DEFAULT 1 NOT NULL,
    source_html text,
    source_hash character varying(64),
    project_sync_disabled boolean DEFAULT false NOT NULL
);


--
-- Name: term_bases; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.term_bases (
    id uuid DEFAULT (((((((((((lpad(to_hex((floor((random() * ('4294967296'::bigint)::double precision)))::bigint), 8, '0'::text) || '-'::text) || lpad(to_hex((floor((random() * (65536)::double precision)))::integer), 4, '0'::text)) || '-'::text) || '4'::text) || substr(lpad(to_hex((floor((random() * (4096)::double precision)))::integer), 3, '0'::text), 1, 3)) || '-'::text) || substr('89ab'::text, ((floor((random() * (4)::double precision)))::integer + 1), 1)) || substr(lpad(to_hex((floor((random() * (4096)::double precision)))::integer), 3, '0'::text), 1, 3)) || '-'::text) || lpad(to_hex((floor((random() * ('281474976710656'::bigint)::double precision)))::bigint), 12, '0'::text)))::uuid NOT NULL,
    name character varying(120) NOT NULL,
    description text,
    source_language character varying(20) NOT NULL,
    target_language character varying(20) NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: term_entries; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.term_entries (
    id uuid DEFAULT (((((((((((lpad(to_hex((floor((random() * ('4294967296'::bigint)::double precision)))::bigint), 8, '0'::text) || '-'::text) || lpad(to_hex((floor((random() * (65536)::double precision)))::integer), 4, '0'::text)) || '-'::text) || '4'::text) || substr(lpad(to_hex((floor((random() * (4096)::double precision)))::integer), 3, '0'::text), 1, 3)) || '-'::text) || substr('89ab'::text, ((floor((random() * (4)::double precision)))::integer + 1), 1)) || substr(lpad(to_hex((floor((random() * (4096)::double precision)))::integer), 3, '0'::text), 1, 3)) || '-'::text) || lpad(to_hex((floor((random() * ('281474976710656'::bigint)::double precision)))::bigint), 12, '0'::text)))::uuid NOT NULL,
    term_base_id uuid NOT NULL,
    source_text text NOT NULL,
    target_text text NOT NULL,
    source_normalized text,
    source_language character varying(20) NOT NULL,
    target_language character varying(20) NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL,
    creator_id uuid
);


--
-- Name: term_qa_report_items; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.term_qa_report_items (
    id uuid DEFAULT (((((((((((lpad(to_hex((floor((random() * ('4294967296'::bigint)::double precision)))::bigint), 8, '0'::text) || '-'::text) || lpad(to_hex((floor((random() * (65536)::double precision)))::integer), 4, '0'::text)) || '-'::text) || '4'::text) || substr(lpad(to_hex((floor((random() * (4096)::double precision)))::integer), 3, '0'::text), 1, 3)) || '-'::text) || substr('89ab'::text, ((floor((random() * (4)::double precision)))::integer + 1), 1)) || substr(lpad(to_hex((floor((random() * (4096)::double precision)))::integer), 3, '0'::text), 1, 3)) || '-'::text) || lpad(to_hex((floor((random() * ('281474976710656'::bigint)::double precision)))::bigint), 12, '0'::text)))::uuid NOT NULL,
    report_id uuid NOT NULL,
    project_id uuid,
    file_record_id uuid NOT NULL,
    segment_id uuid,
    term_base_id uuid,
    sentence_id character varying(40) DEFAULT ''::character varying NOT NULL,
    file_name character varying(255) DEFAULT ''::character varying NOT NULL,
    term_base_name character varying(200) DEFAULT ''::character varying NOT NULL,
    source_term text NOT NULL,
    expected_target_term text NOT NULL,
    source_text text NOT NULL,
    target_text text DEFAULT ''::text NOT NULL,
    block_index integer DEFAULT 0 NOT NULL,
    row_index integer,
    cell_index integer,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    ignored_by_id uuid,
    ignored_at timestamp without time zone
);


--
-- Name: term_qa_reports; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.term_qa_reports (
    id uuid DEFAULT (((((((((((lpad(to_hex((floor((random() * ('4294967296'::bigint)::double precision)))::bigint), 8, '0'::text) || '-'::text) || lpad(to_hex((floor((random() * (65536)::double precision)))::integer), 4, '0'::text)) || '-'::text) || '4'::text) || substr(lpad(to_hex((floor((random() * (4096)::double precision)))::integer), 3, '0'::text), 1, 3)) || '-'::text) || substr('89ab'::text, ((floor((random() * (4)::double precision)))::integer + 1), 1)) || substr(lpad(to_hex((floor((random() * (4096)::double precision)))::integer), 3, '0'::text), 1, 3)) || '-'::text) || lpad(to_hex((floor((random() * ('281474976710656'::bigint)::double precision)))::bigint), 12, '0'::text)))::uuid NOT NULL,
    project_id uuid,
    file_record_id uuid,
    created_by_id uuid,
    scope character varying(20) DEFAULT 'project'::character varying NOT NULL,
    file_ids text DEFAULT '[]'::text NOT NULL,
    term_base_ids text DEFAULT '[]'::text NOT NULL,
    language_pairs text DEFAULT '[]'::text NOT NULL,
    total_files integer DEFAULT 0 NOT NULL,
    total_segments integer DEFAULT 0 NOT NULL,
    checked_segments integer DEFAULT 0 NOT NULL,
    issue_count integer DEFAULT 0 NOT NULL,
    status character varying(20) DEFAULT 'completed'::character varying NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: translation_metric_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.translation_metric_events (
    id uuid DEFAULT (((((((((((lpad(to_hex((floor((random() * ('4294967296'::bigint)::double precision)))::bigint), 8, '0'::text) || '-'::text) || lpad(to_hex((floor((random() * (65536)::double precision)))::integer), 4, '0'::text)) || '-'::text) || '4'::text) || substr(lpad(to_hex((floor((random() * (4096)::double precision)))::integer), 3, '0'::text), 1, 3)) || '-'::text) || substr('89ab'::text, ((floor((random() * (4)::double precision)))::integer + 1), 1)) || substr(lpad(to_hex((floor((random() * (4096)::double precision)))::integer), 3, '0'::text), 1, 3)) || '-'::text) || lpad(to_hex((floor((random() * ('281474976710656'::bigint)::double precision)))::bigint), 12, '0'::text)))::uuid NOT NULL,
    event_key character varying(140),
    project_id uuid,
    file_record_id uuid,
    segment_id uuid,
    user_id uuid,
    source character varying(20) DEFAULT 'manual'::character varying NOT NULL,
    source_language character varying(20),
    target_language character varying(20),
    source_word_count integer DEFAULT 0 NOT NULL,
    target_was_empty boolean DEFAULT true NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: user_activity_daily; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_activity_daily (
    id uuid DEFAULT (((((((((((lpad(to_hex((floor((random() * ('4294967296'::bigint)::double precision)))::bigint), 8, '0'::text) || '-'::text) || lpad(to_hex((floor((random() * (65536)::double precision)))::integer), 4, '0'::text)) || '-'::text) || '4'::text) || substr(lpad(to_hex((floor((random() * (4096)::double precision)))::integer), 3, '0'::text), 1, 3)) || '-'::text) || substr('89ab'::text, ((floor((random() * (4)::double precision)))::integer + 1), 1)) || substr(lpad(to_hex((floor((random() * (4096)::double precision)))::integer), 3, '0'::text), 1, 3)) || '-'::text) || lpad(to_hex((floor((random() * ('281474976710656'::bigint)::double precision)))::bigint), 12, '0'::text)))::uuid NOT NULL,
    user_id uuid NOT NULL,
    activity_date date NOT NULL,
    request_count integer DEFAULT 0 NOT NULL,
    first_seen_at timestamp without time zone DEFAULT now() NOT NULL,
    last_seen_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.users (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    username character varying(50) NOT NULL,
    hashed_password character varying(255) NOT NULL,
    role character varying(20) DEFAULT 'user'::character varying NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    nickname character varying(50),
    translator_type character varying(20) DEFAULT 'internal'::character varying NOT NULL
);


--
-- Name: assignment_events assignment_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assignment_events
    ADD CONSTRAINT assignment_events_pkey PRIMARY KEY (id);


--
-- Name: auto_tm_outbox auto_tm_outbox_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.auto_tm_outbox
    ADD CONSTRAINT auto_tm_outbox_pkey PRIMARY KEY (id);


--
-- Name: auto_tm_rematch_queue auto_tm_rematch_queue_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.auto_tm_rematch_queue
    ADD CONSTRAINT auto_tm_rematch_queue_pkey PRIMARY KEY (id);


--
-- Name: document_statistics_report_items document_statistics_report_items_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.document_statistics_report_items
    ADD CONSTRAINT document_statistics_report_items_pkey PRIMARY KEY (id);


--
-- Name: document_statistics_reports document_statistics_reports_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.document_statistics_reports
    ADD CONSTRAINT document_statistics_reports_pkey PRIMARY KEY (id);


--
-- Name: file_assignments file_assignments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.file_assignments
    ADD CONSTRAINT file_assignments_pkey PRIMARY KEY (id);


--
-- Name: file_records file_records_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.file_records
    ADD CONSTRAINT file_records_pkey PRIMARY KEY (id);


--
-- Name: glossary_bases glossary_bases_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.glossary_bases
    ADD CONSTRAINT glossary_bases_pkey PRIMARY KEY (id);


--
-- Name: glossary_entries glossary_entries_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.glossary_entries
    ADD CONSTRAINT glossary_entries_pkey PRIMARY KEY (id);


--
-- Name: issue_markers issue_markers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.issue_markers
    ADD CONSTRAINT issue_markers_pkey PRIMARY KEY (id);


--
-- Name: memory_bases memory_bases_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.memory_bases
    ADD CONSTRAINT memory_bases_pkey PRIMARY KEY (id);


--
-- Name: memory_entries memory_entries_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.memory_entries
    ADD CONSTRAINT memory_entries_pkey PRIMARY KEY (id);


--
-- Name: notifications notifications_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notifications
    ADD CONSTRAINT notifications_pkey PRIMARY KEY (id);


--
-- Name: project_assignments project_assignments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.project_assignments
    ADD CONSTRAINT project_assignments_pkey PRIMARY KEY (id);


--
-- Name: projects projects_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.projects
    ADD CONSTRAINT projects_pkey PRIMARY KEY (id);


--
-- Name: reference_files reference_files_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reference_files
    ADD CONSTRAINT reference_files_pkey PRIMARY KEY (id);


--
-- Name: reference_profiles reference_profiles_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reference_profiles
    ADD CONSTRAINT reference_profiles_pkey PRIMARY KEY (id);


--
-- Name: segment_comments segment_comments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.segment_comments
    ADD CONSTRAINT segment_comments_pkey PRIMARY KEY (id);


--
-- Name: segment_history segment_history_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.segment_history
    ADD CONSTRAINT segment_history_pkey PRIMARY KEY (id);


--
-- Name: segment_revisions segment_revisions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.segment_revisions
    ADD CONSTRAINT segment_revisions_pkey PRIMARY KEY (id);


--
-- Name: segments segments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.segments
    ADD CONSTRAINT segments_pkey PRIMARY KEY (id);


--
-- Name: term_bases term_bases_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.term_bases
    ADD CONSTRAINT term_bases_pkey PRIMARY KEY (id);


--
-- Name: term_entries term_entries_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.term_entries
    ADD CONSTRAINT term_entries_pkey PRIMARY KEY (id);


--
-- Name: term_qa_report_items term_qa_report_items_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.term_qa_report_items
    ADD CONSTRAINT term_qa_report_items_pkey PRIMARY KEY (id);


--
-- Name: term_qa_reports term_qa_reports_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.term_qa_reports
    ADD CONSTRAINT term_qa_reports_pkey PRIMARY KEY (id);


--
-- Name: translation_metric_events translation_metric_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.translation_metric_events
    ADD CONSTRAINT translation_metric_events_pkey PRIMARY KEY (id);


--
-- Name: user_activity_daily user_activity_daily_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_activity_daily
    ADD CONSTRAINT user_activity_daily_pkey PRIMARY KEY (id);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);


--
-- Name: users users_username_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_username_key UNIQUE (username);


--
-- Name: ix_assignment_events_actor_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_assignment_events_actor_id ON public.assignment_events USING btree (actor_id);


--
-- Name: ix_assignment_events_assignee_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_assignment_events_assignee_id ON public.assignment_events USING btree (assignee_id);


--
-- Name: ix_assignment_events_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_assignment_events_created_at ON public.assignment_events USING btree (created_at);


--
-- Name: ix_assignment_events_file_record_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_assignment_events_file_record_id ON public.assignment_events USING btree (file_record_id);


--
-- Name: ix_assignment_events_project_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_assignment_events_project_id ON public.assignment_events USING btree (project_id);


--
-- Name: ix_auto_tm_outbox_file_record_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_auto_tm_outbox_file_record_id ON public.auto_tm_outbox USING btree (file_record_id);


--
-- Name: ix_auto_tm_outbox_status_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_auto_tm_outbox_status_created_at ON public.auto_tm_outbox USING btree (status, created_at);


--
-- Name: ix_auto_tm_rematch_queue_first_pending_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_auto_tm_rematch_queue_first_pending_at ON public.auto_tm_rematch_queue USING btree (first_pending_at);


--
-- Name: ix_auto_tm_rematch_queue_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_auto_tm_rematch_queue_status ON public.auto_tm_rematch_queue USING btree (status);


--
-- Name: ix_document_statistics_report_items_file_record_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_document_statistics_report_items_file_record_id ON public.document_statistics_report_items USING btree (file_record_id);


--
-- Name: ix_document_statistics_report_items_project_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_document_statistics_report_items_project_id ON public.document_statistics_report_items USING btree (project_id);


--
-- Name: ix_document_statistics_report_items_report_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_document_statistics_report_items_report_id ON public.document_statistics_report_items USING btree (report_id);


--
-- Name: ix_document_statistics_reports_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_document_statistics_reports_created_at ON public.document_statistics_reports USING btree (created_at);


--
-- Name: ix_document_statistics_reports_created_by_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_document_statistics_reports_created_by_id ON public.document_statistics_reports USING btree (created_by_id);


--
-- Name: ix_document_statistics_reports_project_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_document_statistics_reports_project_id ON public.document_statistics_reports USING btree (project_id);


--
-- Name: ix_file_assignments_assignee_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_file_assignments_assignee_id ON public.file_assignments USING btree (assignee_id);


--
-- Name: ix_file_assignments_file_record_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_file_assignments_file_record_id ON public.file_assignments USING btree (file_record_id);


--
-- Name: ix_file_assignments_project_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_file_assignments_project_id ON public.file_assignments USING btree (project_id);


--
-- Name: ix_file_assignments_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_file_assignments_status ON public.file_assignments USING btree (status);


--
-- Name: ix_file_records_active_operation; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_file_records_active_operation ON public.file_records USING btree (active_operation);


--
-- Name: ix_file_records_assignee_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_file_records_assignee_id ON public.file_records USING btree (assignee_id);


--
-- Name: ix_file_records_creator_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_file_records_creator_id ON public.file_records USING btree (creator_id);


--
-- Name: ix_file_records_project_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_file_records_project_id ON public.file_records USING btree (project_id);


--
-- Name: ix_file_records_source_language; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_file_records_source_language ON public.file_records USING btree (source_language);


--
-- Name: ix_file_records_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_file_records_status ON public.file_records USING btree (status);


--
-- Name: ix_glossary_bases_language_pair; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_glossary_bases_language_pair ON public.glossary_bases USING btree (source_language, target_language);


--
-- Name: ix_glossary_entries_base_source_normalized; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_glossary_entries_base_source_normalized ON public.glossary_entries USING btree (glossary_base_id, source_normalized);


--
-- Name: ix_glossary_entries_base_source_text; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_glossary_entries_base_source_text ON public.glossary_entries USING btree (glossary_base_id, source_text);


--
-- Name: ix_glossary_entries_creator_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_glossary_entries_creator_id ON public.glossary_entries USING btree (creator_id);


--
-- Name: ix_glossary_entries_glossary_base_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_glossary_entries_glossary_base_id ON public.glossary_entries USING btree (glossary_base_id);


--
-- Name: ix_glossary_entries_language_pair; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_glossary_entries_language_pair ON public.glossary_entries USING btree (source_language, target_language);


--
-- Name: ix_issue_markers_file_record_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_issue_markers_file_record_id ON public.issue_markers USING btree (file_record_id);


--
-- Name: ix_issue_markers_project_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_issue_markers_project_id ON public.issue_markers USING btree (project_id);


--
-- Name: ix_issue_markers_reporter_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_issue_markers_reporter_id ON public.issue_markers USING btree (reporter_id);


--
-- Name: ix_issue_markers_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_issue_markers_status ON public.issue_markers USING btree (status);


--
-- Name: ix_memory_bases_language_pair; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_memory_bases_language_pair ON public.memory_bases USING btree (source_language, target_language);


--
-- Name: ix_memory_entries_collection_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_memory_entries_collection_id ON public.memory_entries USING btree (collection_id);


--
-- Name: ix_memory_entries_collection_language_pair; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_memory_entries_collection_language_pair ON public.memory_entries USING btree (collection_id, source_language, target_language);


--
-- Name: ix_memory_entries_collection_source_hash; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_memory_entries_collection_source_hash ON public.memory_entries USING btree (collection_id, source_hash);


--
-- Name: ix_memory_entries_collection_source_normalized; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_memory_entries_collection_source_normalized ON public.memory_entries USING btree (collection_id, source_normalized);


--
-- Name: ix_memory_entries_creator_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_memory_entries_creator_id ON public.memory_entries USING btree (creator_id);


--
-- Name: ix_memory_entries_language_pair; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_memory_entries_language_pair ON public.memory_entries USING btree (source_language, target_language);


--
-- Name: ix_memory_entries_source_hash; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_memory_entries_source_hash ON public.memory_entries USING btree (source_hash);


--
-- Name: ix_memory_entries_source_normalized; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_memory_entries_source_normalized ON public.memory_entries USING btree (source_normalized);


--
-- Name: ix_memory_entries_source_normalized_gist_trgm; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_memory_entries_source_normalized_gist_trgm ON public.memory_entries USING gist (source_normalized public.gist_trgm_ops);


--
-- Name: ix_memory_entries_source_normalized_trgm; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_memory_entries_source_normalized_trgm ON public.memory_entries USING gin (source_normalized public.gin_trgm_ops);


--
-- Name: ix_memory_entries_source_text; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_memory_entries_source_text ON public.memory_entries USING btree (source_text);


--
-- Name: ix_memory_entries_source_text_trgm; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_memory_entries_source_text_trgm ON public.memory_entries USING gin (source_text public.gin_trgm_ops);


--
-- Name: ix_notifications_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_notifications_created_at ON public.notifications USING btree (created_at);


--
-- Name: ix_notifications_read_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_notifications_read_at ON public.notifications USING btree (read_at);


--
-- Name: ix_notifications_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_notifications_user_id ON public.notifications USING btree (user_id);


--
-- Name: ix_project_assignments_assignee_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_project_assignments_assignee_id ON public.project_assignments USING btree (assignee_id);


--
-- Name: ix_project_assignments_project_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_project_assignments_project_id ON public.project_assignments USING btree (project_id);


--
-- Name: ix_project_assignments_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_project_assignments_status ON public.project_assignments USING btree (status);


--
-- Name: ix_projects_creator_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_projects_creator_id ON public.projects USING btree (creator_id);


--
-- Name: ix_projects_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_projects_status ON public.projects USING btree (status);


--
-- Name: ix_reference_files_profile_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_reference_files_profile_id ON public.reference_files USING btree (profile_id);


--
-- Name: ix_reference_profiles_file_record_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_reference_profiles_file_record_id ON public.reference_profiles USING btree (file_record_id);


--
-- Name: ix_segment_comments_file_record_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_segment_comments_file_record_id ON public.segment_comments USING btree (file_record_id);


--
-- Name: ix_segment_comments_parent_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_segment_comments_parent_id ON public.segment_comments USING btree (parent_id);


--
-- Name: ix_segment_comments_segment_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_segment_comments_segment_id ON public.segment_comments USING btree (segment_id);


--
-- Name: ix_segment_history_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_segment_history_created_at ON public.segment_history USING btree (created_at DESC);


--
-- Name: ix_segment_history_file_record_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_segment_history_file_record_id ON public.segment_history USING btree (file_record_id);


--
-- Name: ix_segment_history_segment_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_segment_history_segment_id ON public.segment_history USING btree (segment_id);


--
-- Name: ix_segment_history_sentence_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_segment_history_sentence_id ON public.segment_history USING btree (sentence_id);


--
-- Name: ix_segment_revisions_file_record_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_segment_revisions_file_record_id ON public.segment_revisions USING btree (file_record_id);


--
-- Name: ix_segment_revisions_segment_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_segment_revisions_segment_id ON public.segment_revisions USING btree (segment_id);


--
-- Name: ix_segment_revisions_sentence_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_segment_revisions_sentence_id ON public.segment_revisions USING btree (sentence_id);


--
-- Name: ix_segment_revisions_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_segment_revisions_status ON public.segment_revisions USING btree (status);


--
-- Name: ix_segments_file_record_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_segments_file_record_id ON public.segments USING btree (file_record_id);


--
-- Name: ix_segments_file_record_order; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_segments_file_record_order ON public.segments USING btree (file_record_id, block_index, row_index, cell_index, sentence_id);


--
-- Name: ix_segments_file_source_hash; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_segments_file_source_hash ON public.segments USING btree (file_record_id, source_hash);


--
-- Name: ix_segments_source_hash; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_segments_source_hash ON public.segments USING btree (source_hash);


--
-- Name: ix_segments_source_word_backfill; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_segments_source_word_backfill ON public.segments USING btree (id) WHERE ((source_word_count = 0) AND (source_text <> ''::text));


--
-- Name: ix_segments_source_word_count; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_segments_source_word_count ON public.segments USING btree (source_word_count);


--
-- Name: ix_segments_translated_backfill; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_segments_translated_backfill ON public.segments USING btree (updated_at, id) WHERE ((target_text <> ''::text) AND (source_word_count > 0));


--
-- Name: ix_segments_translated_source_word_count; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_segments_translated_source_word_count ON public.segments USING btree (file_record_id, source_word_count) WHERE ((target_text <> ''::text) AND (source_word_count > 0));


--
-- Name: ix_term_bases_language_pair; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_term_bases_language_pair ON public.term_bases USING btree (source_language, target_language);


--
-- Name: ix_term_entries_creator_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_term_entries_creator_id ON public.term_entries USING btree (creator_id);


--
-- Name: ix_term_entries_language_pair; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_term_entries_language_pair ON public.term_entries USING btree (source_language, target_language);


--
-- Name: ix_term_entries_term_base_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_term_entries_term_base_id ON public.term_entries USING btree (term_base_id);


--
-- Name: ix_term_entries_term_base_source_normalized; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_term_entries_term_base_source_normalized ON public.term_entries USING btree (term_base_id, source_normalized);


--
-- Name: ix_term_entries_term_base_source_text; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_term_entries_term_base_source_text ON public.term_entries USING btree (term_base_id, source_text);


--
-- Name: ix_term_qa_report_items_file_record_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_term_qa_report_items_file_record_id ON public.term_qa_report_items USING btree (file_record_id);


--
-- Name: ix_term_qa_report_items_ignored_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_term_qa_report_items_ignored_at ON public.term_qa_report_items USING btree (ignored_at);


--
-- Name: ix_term_qa_report_items_project_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_term_qa_report_items_project_id ON public.term_qa_report_items USING btree (project_id);


--
-- Name: ix_term_qa_report_items_report_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_term_qa_report_items_report_id ON public.term_qa_report_items USING btree (report_id);


--
-- Name: ix_term_qa_report_items_segment_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_term_qa_report_items_segment_id ON public.term_qa_report_items USING btree (segment_id);


--
-- Name: ix_term_qa_report_items_term_base_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_term_qa_report_items_term_base_id ON public.term_qa_report_items USING btree (term_base_id);


--
-- Name: ix_term_qa_reports_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_term_qa_reports_created_at ON public.term_qa_reports USING btree (created_at);


--
-- Name: ix_term_qa_reports_created_by_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_term_qa_reports_created_by_id ON public.term_qa_reports USING btree (created_by_id);


--
-- Name: ix_term_qa_reports_file_record_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_term_qa_reports_file_record_id ON public.term_qa_reports USING btree (file_record_id);


--
-- Name: ix_term_qa_reports_project_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_term_qa_reports_project_id ON public.term_qa_reports USING btree (project_id);


--
-- Name: ix_translation_metric_events_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_translation_metric_events_created_at ON public.translation_metric_events USING btree (created_at);


--
-- Name: ix_translation_metric_events_file_record_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_translation_metric_events_file_record_id ON public.translation_metric_events USING btree (file_record_id);


--
-- Name: ix_translation_metric_events_language_pair; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_translation_metric_events_language_pair ON public.translation_metric_events USING btree (source_language, target_language);


--
-- Name: ix_translation_metric_events_segment_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_translation_metric_events_segment_id ON public.translation_metric_events USING btree (segment_id);


--
-- Name: ix_translation_metric_events_source; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_translation_metric_events_source ON public.translation_metric_events USING btree (source);


--
-- Name: ix_translation_metric_events_source_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_translation_metric_events_source_created_at ON public.translation_metric_events USING btree (source, created_at);


--
-- Name: ix_user_activity_daily_activity_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_user_activity_daily_activity_date ON public.user_activity_daily USING btree (activity_date);


--
-- Name: ix_user_activity_daily_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_user_activity_daily_user_id ON public.user_activity_daily USING btree (user_id);


--
-- Name: ix_users_username; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_users_username ON public.users USING btree (username);


--
-- Name: uq_auto_tm_outbox_file_segment_collection; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_auto_tm_outbox_file_segment_collection ON public.auto_tm_outbox USING btree (file_record_id, segment_id, collection_id);


--
-- Name: uq_auto_tm_rematch_queue_file_record; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_auto_tm_rematch_queue_file_record ON public.auto_tm_rematch_queue USING btree (file_record_id);


--
-- Name: uq_file_assignments_active_file_user; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_file_assignments_active_file_user ON public.file_assignments USING btree (file_record_id, assignee_id) WHERE ((status)::text = 'active'::text);


--
-- Name: uq_glossary_bases_name; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_glossary_bases_name ON public.glossary_bases USING btree (name);


--
-- Name: uq_memory_bases_name; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_memory_bases_name ON public.memory_bases USING btree (name);


--
-- Name: uq_memory_entries_collection_source_hash_language_pair; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_memory_entries_collection_source_hash_language_pair ON public.memory_entries USING btree (collection_id, source_hash, source_language, target_language);


--
-- Name: uq_project_assignments_active_project_user; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_project_assignments_active_project_user ON public.project_assignments USING btree (project_id, assignee_id) WHERE ((status)::text = 'active'::text);


--
-- Name: uq_term_bases_name; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_term_bases_name ON public.term_bases USING btree (name);


--
-- Name: uq_translation_metric_events_event_key; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_translation_metric_events_event_key ON public.translation_metric_events USING btree (event_key) WHERE (event_key IS NOT NULL);


--
-- Name: uq_user_activity_daily_user_date; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_user_activity_daily_user_date ON public.user_activity_daily USING btree (user_id, activity_date);


--
-- Name: reference_profiles trigger_reference_profiles_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trigger_reference_profiles_updated_at BEFORE UPDATE ON public.reference_profiles FOR EACH ROW EXECUTE FUNCTION public.update_reference_profiles_updated_at();


--
-- Name: file_records update_file_records_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER update_file_records_updated_at BEFORE UPDATE ON public.file_records FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: glossary_bases update_glossary_bases_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER update_glossary_bases_updated_at BEFORE UPDATE ON public.glossary_bases FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: glossary_entries update_glossary_entries_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER update_glossary_entries_updated_at BEFORE UPDATE ON public.glossary_entries FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: issue_markers update_issue_markers_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER update_issue_markers_updated_at BEFORE UPDATE ON public.issue_markers FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: memory_bases update_memory_bases_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER update_memory_bases_updated_at BEFORE UPDATE ON public.memory_bases FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: memory_entries update_memory_entries_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER update_memory_entries_updated_at BEFORE UPDATE ON public.memory_entries FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: projects update_projects_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER update_projects_updated_at BEFORE UPDATE ON public.projects FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: segment_comments update_segment_comments_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER update_segment_comments_updated_at BEFORE UPDATE ON public.segment_comments FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: segments update_segments_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER update_segments_updated_at BEFORE UPDATE ON public.segments FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: term_bases update_term_bases_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER update_term_bases_updated_at BEFORE UPDATE ON public.term_bases FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: term_entries update_term_entries_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER update_term_entries_updated_at BEFORE UPDATE ON public.term_entries FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: assignment_events assignment_events_actor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assignment_events
    ADD CONSTRAINT assignment_events_actor_id_fkey FOREIGN KEY (actor_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: assignment_events assignment_events_assignee_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assignment_events
    ADD CONSTRAINT assignment_events_assignee_id_fkey FOREIGN KEY (assignee_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: assignment_events assignment_events_file_record_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assignment_events
    ADD CONSTRAINT assignment_events_file_record_id_fkey FOREIGN KEY (file_record_id) REFERENCES public.file_records(id) ON DELETE CASCADE;


--
-- Name: assignment_events assignment_events_project_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assignment_events
    ADD CONSTRAINT assignment_events_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.projects(id) ON DELETE CASCADE;


--
-- Name: auto_tm_outbox auto_tm_outbox_collection_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.auto_tm_outbox
    ADD CONSTRAINT auto_tm_outbox_collection_id_fkey FOREIGN KEY (collection_id) REFERENCES public.memory_bases(id) ON DELETE CASCADE;


--
-- Name: auto_tm_outbox auto_tm_outbox_creator_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.auto_tm_outbox
    ADD CONSTRAINT auto_tm_outbox_creator_id_fkey FOREIGN KEY (creator_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: auto_tm_outbox auto_tm_outbox_file_record_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.auto_tm_outbox
    ADD CONSTRAINT auto_tm_outbox_file_record_id_fkey FOREIGN KEY (file_record_id) REFERENCES public.file_records(id) ON DELETE CASCADE;


--
-- Name: auto_tm_outbox auto_tm_outbox_segment_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.auto_tm_outbox
    ADD CONSTRAINT auto_tm_outbox_segment_id_fkey FOREIGN KEY (segment_id) REFERENCES public.segments(id) ON DELETE CASCADE;


--
-- Name: auto_tm_rematch_queue auto_tm_rematch_queue_collection_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.auto_tm_rematch_queue
    ADD CONSTRAINT auto_tm_rematch_queue_collection_id_fkey FOREIGN KEY (collection_id) REFERENCES public.memory_bases(id) ON DELETE CASCADE;


--
-- Name: auto_tm_rematch_queue auto_tm_rematch_queue_file_record_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.auto_tm_rematch_queue
    ADD CONSTRAINT auto_tm_rematch_queue_file_record_id_fkey FOREIGN KEY (file_record_id) REFERENCES public.file_records(id) ON DELETE CASCADE;


--
-- Name: document_statistics_report_items document_statistics_report_items_file_record_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.document_statistics_report_items
    ADD CONSTRAINT document_statistics_report_items_file_record_id_fkey FOREIGN KEY (file_record_id) REFERENCES public.file_records(id) ON DELETE SET NULL;


--
-- Name: document_statistics_report_items document_statistics_report_items_project_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.document_statistics_report_items
    ADD CONSTRAINT document_statistics_report_items_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.projects(id) ON DELETE CASCADE;


--
-- Name: document_statistics_report_items document_statistics_report_items_report_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.document_statistics_report_items
    ADD CONSTRAINT document_statistics_report_items_report_id_fkey FOREIGN KEY (report_id) REFERENCES public.document_statistics_reports(id) ON DELETE CASCADE;


--
-- Name: document_statistics_reports document_statistics_reports_created_by_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.document_statistics_reports
    ADD CONSTRAINT document_statistics_reports_created_by_id_fkey FOREIGN KEY (created_by_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: document_statistics_reports document_statistics_reports_project_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.document_statistics_reports
    ADD CONSTRAINT document_statistics_reports_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.projects(id) ON DELETE CASCADE;


--
-- Name: file_assignments file_assignments_assigned_by_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.file_assignments
    ADD CONSTRAINT file_assignments_assigned_by_id_fkey FOREIGN KEY (assigned_by_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: file_assignments file_assignments_assignee_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.file_assignments
    ADD CONSTRAINT file_assignments_assignee_id_fkey FOREIGN KEY (assignee_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: file_assignments file_assignments_file_record_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.file_assignments
    ADD CONSTRAINT file_assignments_file_record_id_fkey FOREIGN KEY (file_record_id) REFERENCES public.file_records(id) ON DELETE CASCADE;


--
-- Name: file_assignments file_assignments_project_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.file_assignments
    ADD CONSTRAINT file_assignments_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.projects(id) ON DELETE CASCADE;


--
-- Name: file_assignments file_assignments_revoked_by_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.file_assignments
    ADD CONSTRAINT file_assignments_revoked_by_id_fkey FOREIGN KEY (revoked_by_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: file_records file_records_active_operation_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.file_records
    ADD CONSTRAINT file_records_active_operation_user_id_fkey FOREIGN KEY (active_operation_user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: file_records file_records_assigned_by_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.file_records
    ADD CONSTRAINT file_records_assigned_by_id_fkey FOREIGN KEY (assigned_by_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: file_records file_records_assignee_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.file_records
    ADD CONSTRAINT file_records_assignee_id_fkey FOREIGN KEY (assignee_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: file_records file_records_collection_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.file_records
    ADD CONSTRAINT file_records_collection_id_fkey FOREIGN KEY (collection_id) REFERENCES public.memory_bases(id) ON DELETE SET NULL;


--
-- Name: file_records file_records_creator_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.file_records
    ADD CONSTRAINT file_records_creator_id_fkey FOREIGN KEY (creator_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: file_records file_records_project_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.file_records
    ADD CONSTRAINT file_records_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.projects(id) ON DELETE CASCADE;


--
-- Name: file_records file_records_term_base_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.file_records
    ADD CONSTRAINT file_records_term_base_id_fkey FOREIGN KEY (term_base_id) REFERENCES public.term_bases(id) ON DELETE SET NULL;


--
-- Name: memory_entries fk_translation_memory_collection_id; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.memory_entries
    ADD CONSTRAINT fk_translation_memory_collection_id FOREIGN KEY (collection_id) REFERENCES public.memory_bases(id) ON DELETE SET NULL;


--
-- Name: glossary_entries glossary_entries_creator_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.glossary_entries
    ADD CONSTRAINT glossary_entries_creator_id_fkey FOREIGN KEY (creator_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: glossary_entries glossary_entries_glossary_base_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.glossary_entries
    ADD CONSTRAINT glossary_entries_glossary_base_id_fkey FOREIGN KEY (glossary_base_id) REFERENCES public.glossary_bases(id) ON DELETE CASCADE;


--
-- Name: issue_markers issue_markers_file_record_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.issue_markers
    ADD CONSTRAINT issue_markers_file_record_id_fkey FOREIGN KEY (file_record_id) REFERENCES public.file_records(id) ON DELETE SET NULL;


--
-- Name: issue_markers issue_markers_project_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.issue_markers
    ADD CONSTRAINT issue_markers_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.projects(id) ON DELETE CASCADE;


--
-- Name: issue_markers issue_markers_reporter_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.issue_markers
    ADD CONSTRAINT issue_markers_reporter_id_fkey FOREIGN KEY (reporter_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: issue_markers issue_markers_resolved_by_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.issue_markers
    ADD CONSTRAINT issue_markers_resolved_by_id_fkey FOREIGN KEY (resolved_by_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: memory_entries memory_entries_creator_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.memory_entries
    ADD CONSTRAINT memory_entries_creator_id_fkey FOREIGN KEY (creator_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: notifications notifications_file_record_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notifications
    ADD CONSTRAINT notifications_file_record_id_fkey FOREIGN KEY (file_record_id) REFERENCES public.file_records(id) ON DELETE CASCADE;


--
-- Name: notifications notifications_project_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notifications
    ADD CONSTRAINT notifications_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.projects(id) ON DELETE CASCADE;


--
-- Name: notifications notifications_related_event_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notifications
    ADD CONSTRAINT notifications_related_event_id_fkey FOREIGN KEY (related_event_id) REFERENCES public.assignment_events(id) ON DELETE SET NULL;


--
-- Name: notifications notifications_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notifications
    ADD CONSTRAINT notifications_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: project_assignments project_assignments_assigned_by_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.project_assignments
    ADD CONSTRAINT project_assignments_assigned_by_id_fkey FOREIGN KEY (assigned_by_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: project_assignments project_assignments_assignee_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.project_assignments
    ADD CONSTRAINT project_assignments_assignee_id_fkey FOREIGN KEY (assignee_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: project_assignments project_assignments_project_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.project_assignments
    ADD CONSTRAINT project_assignments_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.projects(id) ON DELETE CASCADE;


--
-- Name: project_assignments project_assignments_revoked_by_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.project_assignments
    ADD CONSTRAINT project_assignments_revoked_by_id_fkey FOREIGN KEY (revoked_by_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: projects projects_creator_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.projects
    ADD CONSTRAINT projects_creator_id_fkey FOREIGN KEY (creator_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: reference_files reference_files_profile_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reference_files
    ADD CONSTRAINT reference_files_profile_id_fkey FOREIGN KEY (profile_id) REFERENCES public.reference_profiles(id) ON DELETE CASCADE;


--
-- Name: reference_profiles reference_profiles_file_record_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reference_profiles
    ADD CONSTRAINT reference_profiles_file_record_id_fkey FOREIGN KEY (file_record_id) REFERENCES public.file_records(id) ON DELETE CASCADE;


--
-- Name: segment_comments segment_comments_author_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.segment_comments
    ADD CONSTRAINT segment_comments_author_id_fkey FOREIGN KEY (author_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: segment_comments segment_comments_file_record_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.segment_comments
    ADD CONSTRAINT segment_comments_file_record_id_fkey FOREIGN KEY (file_record_id) REFERENCES public.file_records(id) ON DELETE CASCADE;


--
-- Name: segment_comments segment_comments_parent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.segment_comments
    ADD CONSTRAINT segment_comments_parent_id_fkey FOREIGN KEY (parent_id) REFERENCES public.segment_comments(id) ON DELETE CASCADE;


--
-- Name: segment_comments segment_comments_segment_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.segment_comments
    ADD CONSTRAINT segment_comments_segment_id_fkey FOREIGN KEY (segment_id) REFERENCES public.segments(id) ON DELETE SET NULL;


--
-- Name: segment_history segment_history_file_record_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.segment_history
    ADD CONSTRAINT segment_history_file_record_id_fkey FOREIGN KEY (file_record_id) REFERENCES public.file_records(id) ON DELETE CASCADE;


--
-- Name: segment_history segment_history_operator_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.segment_history
    ADD CONSTRAINT segment_history_operator_id_fkey FOREIGN KEY (operator_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: segment_history segment_history_segment_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.segment_history
    ADD CONSTRAINT segment_history_segment_id_fkey FOREIGN KEY (segment_id) REFERENCES public.segments(id) ON DELETE CASCADE;


--
-- Name: segment_revisions segment_revisions_author_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.segment_revisions
    ADD CONSTRAINT segment_revisions_author_id_fkey FOREIGN KEY (author_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: segment_revisions segment_revisions_file_record_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.segment_revisions
    ADD CONSTRAINT segment_revisions_file_record_id_fkey FOREIGN KEY (file_record_id) REFERENCES public.file_records(id) ON DELETE CASCADE;


--
-- Name: segment_revisions segment_revisions_resolved_by_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.segment_revisions
    ADD CONSTRAINT segment_revisions_resolved_by_id_fkey FOREIGN KEY (resolved_by_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: segment_revisions segment_revisions_segment_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.segment_revisions
    ADD CONSTRAINT segment_revisions_segment_id_fkey FOREIGN KEY (segment_id) REFERENCES public.segments(id) ON DELETE CASCADE;


--
-- Name: segments segments_file_record_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.segments
    ADD CONSTRAINT segments_file_record_id_fkey FOREIGN KEY (file_record_id) REFERENCES public.file_records(id) ON DELETE CASCADE;


--
-- Name: term_entries term_entries_creator_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.term_entries
    ADD CONSTRAINT term_entries_creator_id_fkey FOREIGN KEY (creator_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: term_entries term_entries_term_base_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.term_entries
    ADD CONSTRAINT term_entries_term_base_id_fkey FOREIGN KEY (term_base_id) REFERENCES public.term_bases(id) ON DELETE CASCADE;


--
-- Name: term_qa_report_items term_qa_report_items_file_record_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.term_qa_report_items
    ADD CONSTRAINT term_qa_report_items_file_record_id_fkey FOREIGN KEY (file_record_id) REFERENCES public.file_records(id) ON DELETE CASCADE;


--
-- Name: term_qa_report_items term_qa_report_items_ignored_by_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.term_qa_report_items
    ADD CONSTRAINT term_qa_report_items_ignored_by_id_fkey FOREIGN KEY (ignored_by_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: term_qa_report_items term_qa_report_items_project_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.term_qa_report_items
    ADD CONSTRAINT term_qa_report_items_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.projects(id) ON DELETE CASCADE;


--
-- Name: term_qa_report_items term_qa_report_items_report_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.term_qa_report_items
    ADD CONSTRAINT term_qa_report_items_report_id_fkey FOREIGN KEY (report_id) REFERENCES public.term_qa_reports(id) ON DELETE CASCADE;


--
-- Name: term_qa_report_items term_qa_report_items_segment_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.term_qa_report_items
    ADD CONSTRAINT term_qa_report_items_segment_id_fkey FOREIGN KEY (segment_id) REFERENCES public.segments(id) ON DELETE SET NULL;


--
-- Name: term_qa_report_items term_qa_report_items_term_base_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.term_qa_report_items
    ADD CONSTRAINT term_qa_report_items_term_base_id_fkey FOREIGN KEY (term_base_id) REFERENCES public.term_bases(id) ON DELETE SET NULL;


--
-- Name: term_qa_reports term_qa_reports_created_by_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.term_qa_reports
    ADD CONSTRAINT term_qa_reports_created_by_id_fkey FOREIGN KEY (created_by_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: term_qa_reports term_qa_reports_file_record_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.term_qa_reports
    ADD CONSTRAINT term_qa_reports_file_record_id_fkey FOREIGN KEY (file_record_id) REFERENCES public.file_records(id) ON DELETE CASCADE;


--
-- Name: term_qa_reports term_qa_reports_project_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.term_qa_reports
    ADD CONSTRAINT term_qa_reports_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.projects(id) ON DELETE CASCADE;


--
-- Name: translation_metric_events translation_metric_events_file_record_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.translation_metric_events
    ADD CONSTRAINT translation_metric_events_file_record_id_fkey FOREIGN KEY (file_record_id) REFERENCES public.file_records(id) ON DELETE SET NULL;


--
-- Name: translation_metric_events translation_metric_events_project_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.translation_metric_events
    ADD CONSTRAINT translation_metric_events_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.projects(id) ON DELETE SET NULL;


--
-- Name: translation_metric_events translation_metric_events_segment_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.translation_metric_events
    ADD CONSTRAINT translation_metric_events_segment_id_fkey FOREIGN KEY (segment_id) REFERENCES public.segments(id) ON DELETE SET NULL;


--
-- Name: translation_metric_events translation_metric_events_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.translation_metric_events
    ADD CONSTRAINT translation_metric_events_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: user_activity_daily user_activity_daily_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_activity_daily
    ADD CONSTRAINT user_activity_daily_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- PostgreSQL database dump complete
--

\unrestrict EEsFTQiNWBGYttpLr5fdHkdpD4kMjrcaM1YnKte41Dj8jAVbPc5jXihy5uq9zqL

