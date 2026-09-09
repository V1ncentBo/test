-- ============================================================
-- 报表系统 v2.0 数据库迁移脚本
-- 在现有 monitor_platform 数据库上执行
-- 执行前请备份: mysqldump -u root -p monitor_platform > backup.sql
-- ============================================================

USE monitor_platform;

-- --------------------------------------
-- 1. 扩展 report_record 表（如果已存在则 ALTER，否则 CREATE）
-- --------------------------------------

-- 先检查表是否存在
SET @table_exists = (SELECT COUNT(*) FROM information_schema.tables
                     WHERE table_schema = 'monitor_platform' AND table_name = 'report_record');

-- 如果表不存在，创建新表
SET @sql_create = IF(@table_exists = 0,
    'CREATE TABLE report_record (
        id INT AUTO_INCREMENT PRIMARY KEY,
        report_type ENUM("daily","weekly","monthly") NOT NULL COMMENT "报表类型",
        title VARCHAR(256) NOT NULL COMMENT "报表标题",
        period_start DATETIME NOT NULL COMMENT "周期开始",
        period_end DATETIME NOT NULL COMMENT "周期结束",
        html_content LONGTEXT COMMENT "HTML 报表内容",
        json_summary JSON COMMENT "JSON 摘要数据",
        ai_analysis TEXT COMMENT "AI 分析结果",
        metrics_snapshot JSON COMMENT "指标快照",
        alert_summary JSON COMMENT "告警汇总",
        device_stats JSON COMMENT "设备统计",
        status ENUM("pending","generating","completed","failed") DEFAULT "pending" COMMENT "状态",
        error_message TEXT COMMENT "错误信息",
        generated_at DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT "生成时间",
        delivery_method ENUM("web","email","dingtalk","all") DEFAULT "web" COMMENT "分发方式",
        delivery_status VARCHAR(64) DEFAULT "pending" COMMENT "分发状态",
        delivered_at DATETIME COMMENT "分发时间",
        created_by VARCHAR(64) DEFAULT "scheduler" COMMENT "创建方式",
        file_path VARCHAR(512) COMMENT "导出文件路径",
        trend_data JSON COMMENT "趋势数据",
        health_scores JSON COMMENT "健康评分",
        capacity_planning JSON COMMENT "容量规划",
        anomaly_patterns JSON COMMENT "异常模式",
        INDEX idx_report_type (report_type),
        INDEX idx_status (status),
        INDEX idx_generated_at (generated_at),
        INDEX idx_period (period_start, period_end)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT="报表记录表"',
    'SELECT "report_record already exists, adding new columns..." AS message');

PREPARE stmt FROM @sql_create;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- 如果表已存在，添加 v2.0 新增字段
SET @col_json = (SELECT COUNT(*) FROM information_schema.columns
                 WHERE table_schema = 'monitor_platform' AND table_name = 'report_record'
                 AND column_name = 'json_summary');
SET @sql_add_json = IF(@table_exists > 0 AND @col_json = 0,
    'ALTER TABLE report_record ADD COLUMN json_summary JSON COMMENT "JSON 摘要数据" AFTER html_content',
    'SELECT "json_summary column already exists" AS message');
PREPARE stmt FROM @sql_add_json; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @col_trend = (SELECT COUNT(*) FROM information_schema.columns
                  WHERE table_schema = 'monitor_platform' AND table_name = 'report_record'
                  AND column_name = 'trend_data');
SET @sql_add_trend = IF(@table_exists > 0 AND @col_trend = 0,
    'ALTER TABLE report_record ADD COLUMN trend_data JSON COMMENT "趋势数据" AFTER file_path',
    'SELECT "trend_data column already exists" AS message');
PREPARE stmt FROM @sql_add_trend; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @col_health = (SELECT COUNT(*) FROM information_schema.columns
                   WHERE table_schema = 'monitor_platform' AND table_name = 'report_record'
                   AND column_name = 'health_scores');
SET @sql_add_health = IF(@table_exists > 0 AND @col_health = 0,
    'ALTER TABLE report_record ADD COLUMN health_scores JSON COMMENT "健康评分" AFTER trend_data',
    'SELECT "health_scores column already exists" AS message');
PREPARE stmt FROM @sql_add_health; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @col_cap = (SELECT COUNT(*) FROM information_schema.columns
                WHERE table_schema = 'monitor_platform' AND table_name = 'report_record'
                AND column_name = 'capacity_planning');
SET @sql_add_cap = IF(@table_exists > 0 AND @col_cap = 0,
    'ALTER TABLE report_record ADD COLUMN capacity_planning JSON COMMENT "容量规划" AFTER health_scores',
    'SELECT "capacity_planning column already exists" AS message');
PREPARE stmt FROM @sql_add_cap; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @col_anomaly = (SELECT COUNT(*) FROM information_schema.columns
                    WHERE table_schema = 'monitor_platform' AND table_name = 'report_record'
                    AND column_name = 'anomaly_patterns');
SET @sql_add_anomaly = IF(@table_exists > 0 AND @col_anomaly = 0,
    'ALTER TABLE report_record ADD COLUMN anomaly_patterns JSON COMMENT "异常模式" AFTER capacity_planning',
    'SELECT "anomaly_patterns column already exists" AS message');
PREPARE stmt FROM @sql_add_anomaly; EXECUTE stmt; DEALLOCATE PREPARE stmt;


-- --------------------------------------
-- 2. 创建 report_schedule 表
-- --------------------------------------
CREATE TABLE IF NOT EXISTS report_schedule (
    id INT AUTO_INCREMENT PRIMARY KEY,
    report_type ENUM("daily","weekly","monthly") NOT NULL UNIQUE COMMENT "报表类型",
    enabled BOOLEAN DEFAULT TRUE COMMENT "是否启用",
    cron_expression VARCHAR(32) COMMENT "Cron 表达式",
    delivery_methods JSON DEFAULT ("[\"web\"]") COMMENT "分发渠道",
    email_recipients JSON DEFAULT ("[]") COMMENT "邮件接收人",
    dingtalk_enabled BOOLEAN DEFAULT FALSE COMMENT "钉钉推送开关",
    ai_enabled BOOLEAN DEFAULT TRUE COMMENT "AI 分析开关",
    ai_model VARCHAR(64) DEFAULT "deepseek-chat" COMMENT "AI 模型",
    retention_days INT DEFAULT 365 COMMENT "保留天数",
    auto_archive BOOLEAN DEFAULT TRUE COMMENT "自动归档",
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT="报表调度配置表";


-- --------------------------------------
-- 3. 创建 report_template 表
-- --------------------------------------
CREATE TABLE IF NOT EXISTS report_template (
    id INT AUTO_INCREMENT PRIMARY KEY,
    report_type ENUM("daily","weekly","monthly") NOT NULL UNIQUE COMMENT "报表类型",
    name VARCHAR(128) COMMENT "模板名称",
    description TEXT COMMENT "模板描述",
    html_template LONGTEXT NOT NULL COMMENT "HTML 模板",
    css_style TEXT COMMENT "CSS 样式",
    chart_configs JSON COMMENT "图表配置",
    version VARCHAR(16) DEFAULT "1.0",
    is_active BOOLEAN DEFAULT TRUE,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT="报表模板表";


-- --------------------------------------
-- 4. 插入默认调度配置
-- --------------------------------------
INSERT INTO report_schedule (report_type, enabled, cron_expression, delivery_methods, ai_enabled)
VALUES
    ("daily",   TRUE, "0 8 * * *",   '["web","dingtalk"]', TRUE),
    ("weekly",  TRUE, "0 9 * * 1",   '["web","dingtalk"]', TRUE),
    ("monthly", TRUE, "0 10 1 * *",  '["web","dingtalk"]', TRUE)
ON DUPLICATE KEY UPDATE
    cron_expression = VALUES(cron_expression),
    updated_at = CURRENT_TIMESTAMP;


-- --------------------------------------
-- 5. 创建索引
-- --------------------------------------
CREATE INDEX IF NOT EXISTS idx_report_generated
    ON report_record(generated_at DESC);

CREATE INDEX IF NOT EXISTS idx_report_type_status
    ON report_record(report_type, status);


-- --------------------------------------
-- 6. (可选) 添加归档清理存储过程
-- --------------------------------------
DELIMITER $$

CREATE PROCEDURE IF NOT EXISTS archive_old_reports(IN retain_days INT)
BEGIN
    -- 软删除：标记过期报表
    UPDATE report_record
    SET status = 'archived'
    WHERE generated_at < DATE_SUB(NOW(), INTERVAL retain_days DAY)
      AND status = 'completed';

    -- 物理删除：清理已归档超过 90 天的
    DELETE FROM report_record
    WHERE status = 'archived'
      AND generated_at < DATE_SUB(NOW(), INTERVAL 90 DAY);

    SELECT ROW_COUNT() AS deleted_count;
END$$

DELIMITER ;


-- ============================================================
-- 验证
-- ============================================================
SELECT 'Migration completed successfully!' AS status;
SELECT TABLE_NAME, TABLE_COMMENT FROM information_schema.tables
WHERE table_schema = 'monitor_platform' AND table_name LIKE 'report_%';
