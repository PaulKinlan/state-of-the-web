// State of the Web — SQLite schema for web-uplift audit results
CREATE TABLE IF NOT EXISTS sites (
    domain TEXT PRIMARY KEY,
    rank INTEGER,
    audited_at TEXT,
    url TEXT,
    final_url TEXT,
    http_status INTEGER,
    overall_score INTEGER,
    verdict TEXT,
    -- Evidence metrics
    lh_performance INTEGER,
    lh_accessibility INTEGER,
    lh_best_practices INTEGER,
    lh_seo INTEGER,
    axe_violations INTEGER,
    heap_size INTEGER,
    cls REAL,
    lcp INTEGER,
    inp INTEGER,
    is_js_shell INTEGER,
    text_chars INTEGER,
    has_viewport INTEGER,
    has_meta_description INTEGER,
    https_only INTEGER,
    hsts INTEGER
);

CREATE TABLE IF NOT EXISTS principle_results (
    domain TEXT,
    principle_id TEXT,
    status TEXT,  -- pass | issues | not-applicable
    confidence TEXT,
    summary TEXT,
    finding_count INTEGER,
    PRIMARY KEY (domain, principle_id)
);

CREATE TABLE IF NOT EXISTS findings (
    domain TEXT,
    principle_id TEXT,
    finding_id TEXT,
    severity TEXT,  -- high | serious | moderate | low
    summary TEXT,
    evidence TEXT
);

-- Aggregate views
CREATE VIEW IF NOT EXISTS principle_summary AS
SELECT principle_id, status, COUNT(*) as count
FROM principle_results
GROUP BY principle_id, status
ORDER BY principle_id, status;

CREATE VIEW IF NOT EXISTS top_issues AS
SELECT principle_id, severity, COUNT(*) as count, 
       GROUP_CONCAT(domain, ', ') as sites
FROM findings
GROUP BY principle_id, severity
ORDER BY count DESC;
