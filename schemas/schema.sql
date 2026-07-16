// State of the Web — SQLite schema for web-uplift audit results
CREATE TABLE IF NOT EXISTS sites (
    site TEXT PRIMARY KEY,
    source TEXT DEFAULT 'cdp',  -- 'gpt' (vision-audited) or 'cdp' (CDP evidence only)
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

CREATE TABLE IF NOT EXISTS principles (
    site TEXT,
    principle_id TEXT,
    status TEXT,  -- pass | issues | not-applicable
    confidence TEXT,
    summary TEXT,
    finding_count INTEGER,
    PRIMARY KEY (site, principle_id)
);

CREATE TABLE IF NOT EXISTS findings (
    site TEXT,
    principle_id TEXT,
    finding_id TEXT,
    severity TEXT,  -- high | serious | moderate | low
    summary TEXT,
    evidence TEXT
);

-- Atomic checks within a principle. New audit reports must emit one row per
-- applicable check so the UI can distinguish a measured pass from no finding.
CREATE TABLE IF NOT EXISTS principle_tests (
    principle_id TEXT,
    test_id TEXT,
    title TEXT,
    method TEXT,
    PRIMARY KEY (principle_id, test_id)
);

CREATE TABLE IF NOT EXISTS test_results (
    site TEXT,
    principle_id TEXT,
    test_id TEXT,
    status TEXT,  -- pass | issues | not-applicable | blocked | not-run
    confidence TEXT,
    summary TEXT,
    evidence TEXT,
    PRIMARY KEY (site, principle_id, test_id)
);

-- Aggregate views
CREATE VIEW IF NOT EXISTS principle_summary AS
SELECT principle_id, status, COUNT(*) as count
FROM principles
GROUP BY principle_id, status
ORDER BY principle_id, status;

CREATE VIEW IF NOT EXISTS top_issues AS
SELECT principle_id, severity, COUNT(*) as count, 
       GROUP_CONCAT(site, ', ') as sites
FROM findings
GROUP BY principle_id, severity
ORDER BY count DESC;
