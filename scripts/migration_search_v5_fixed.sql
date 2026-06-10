-- ===================================================================
-- LEGAL AI SEARCH V5 - FIXED & OPTIMIZED
-- Target: <1s response, accurate Vietnamese legal search
-- Fixes: NULL rank issue, better relevance scoring
-- ===================================================================

DROP FUNCTION IF EXISTS search_law(text, legal_domain[], int);

CREATE OR REPLACE FUNCTION search_law(
    query_text TEXT,
    filter_domains legal_domain[] DEFAULT NULL,
    match_count INT DEFAULT 20
)
RETURNS TABLE(
    chunk_id UUID,
    law_id UUID,
    law_title TEXT,
    law_number TEXT,
    article TEXT,
    chunk_title TEXT,
    content TEXT,
    domains legal_domain[],
    rank FLOAT
) AS $$
DECLARE
    normalized_query TEXT;
    key_words TEXT[];
    stop_words TEXT[] := ARRAY[
        'là', 'của', 'có', 'và', 'các', 'này', 'đó', 'cho', 'với',
        'trong', 'không', 'được', 'theo', 'về', 'khi', 'nào', 'bao',
        'nhiêu', 'lâu', 'thế', 'như', 'gì', 'tại', 'từ', 'đến',
        'hay', 'hoặc', 'nhưng', 'mà', 'thì', 'nếu', 'bị', 'đã',
        'sẽ', 'đang', 'vậy', 'rồi', 'cần', 'phải', 'tối', 'đa',
        'một', 'hai', 'ba', 'bốn', 'năm'
    ];
    -- Vietnamese compound phrases to detect (flat 1-D array — only the phrase is
    -- used in the bonus below; a ragged TEXT[][] literal crashes at runtime).
    compound_map TEXT[];
    phrase TEXT;
    tsquery_obj TSQUERY;
BEGIN
    normalized_query := lower(trim(query_text));
    
    -- Define common legal phrases
    compound_map := ARRAY[
        'hợp đồng lao động',
        'bảo hiểm xã hội',
        'công ty cổ phần',
        'thuế thu nhập',
        'người lao động',
        'sa thải',
        'thành lập',
        'xác định thời hạn',
        'loại hợp đồng'
    ];
    
    -- Extract keywords
    SELECT array_agg(w) INTO key_words
    FROM (
        SELECT unnest(regexp_split_to_array(normalized_query, '\s+')) as w
    ) t
    WHERE length(w) > 1 AND w != ALL(stop_words);
    
    IF key_words IS NULL OR array_length(key_words, 1) = 0 THEN
        key_words := ARRAY[normalized_query];
    END IF;
    
    -- Limit keywords
    IF array_length(key_words, 1) > 6 THEN
        key_words := key_words[1:6];
    END IF;
    
    -- Build tsquery
    tsquery_obj := to_tsquery('simple', 
        array_to_string(
            (SELECT array_agg(w || ':*') FROM unnest(key_words) w),
            ' | '
        )
    );
    
    RETURN QUERY
    SELECT 
        lc.id AS chunk_id,
        lc.law_id,
        ld.title AS law_title,
        ld.law_number,
        lc.article,
        lc.title AS chunk_title,
        lc.content,
        lc.domains,
        -- Comprehensive scoring (ensure no NULLs)
        (
            COALESCE(
                -- Exact phrase bonus (Vietnamese compound phrases)
                (SELECT SUM(
                    CASE 
                        WHEN lc.content ILIKE '%' || cm || '%' THEN 25.0
                        ELSE 0.0
                    END
                ) FROM unnest(compound_map) AS cm) 
                , 0.0)
            +
            -- Full-text search rank
            COALESCE(ts_rank(lc.tsv, tsquery_obj, 1) * 15.0, 0.0)
            +
            -- Law title domain match (boost relevant law types)
            (CASE 
                WHEN normalized_query LIKE '%lao động%' AND ld.title ILIKE '%lao động%' THEN 8.0
                WHEN normalized_query LIKE '%doanh nghiệp%' AND ld.title ILIKE '%doanh nghiệp%' THEN 8.0
                WHEN normalized_query LIKE '%công ty%' AND ld.title ILIKE '%doanh nghiệp%' THEN 8.0
                WHEN normalized_query LIKE '%thuế%' AND ld.title ILIKE '%thuế%' THEN 8.0
                WHEN normalized_query LIKE '%hợp đồng%' AND ld.title ILIKE '%lao động%' THEN 5.0
                ELSE 0.0
            END)
            +
            -- Article query bonus
            (CASE 
                WHEN normalized_query LIKE '%điều%' AND lc.article IS NOT NULL THEN 2.0
                ELSE 0.0
            END)
            +
            -- Keyword density in content
            ((SELECT COUNT(*)::FLOAT 
              FROM unnest(key_words) kw 
              WHERE lc.content ILIKE '%' || kw || '%') / GREATEST(array_length(key_words, 1), 1)::FLOAT) * 5.0
        ) 
        * 
        -- Length normalization (prefer concise, relevant chunks)
        (CASE 
            WHEN length(lc.content) < 100 THEN 0.6
            WHEN length(lc.content) BETWEEN 100 AND 800 THEN 1.0
            WHEN length(lc.content) BETWEEN 800 AND 2000 THEN 0.9
            ELSE 0.7
        END)
        AS rank
    FROM law_chunks lc
    INNER JOIN law_documents ld ON ld.id = lc.law_id
    WHERE 
        (filter_domains IS NULL OR lc.domains && filter_domains)
        AND lc.tsv @@ tsquery_obj
    ORDER BY rank DESC NULLS LAST, length(lc.content) ASC
    LIMIT match_count;
END;
$$ LANGUAGE plpgsql;

-- Ensure indexes
CREATE INDEX IF NOT EXISTS idx_law_chunks_tsv ON law_chunks USING gin(tsv);
CREATE INDEX IF NOT EXISTS idx_law_chunks_content_trgm ON law_chunks USING gin(content gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_law_docs_title_lower ON law_documents(lower(title));

ANALYZE law_chunks;
ANALYZE law_documents;

SELECT 'Search V5 deployed - fixed NULL rank, improved relevance scoring.' AS status;
