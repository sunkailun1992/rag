-- rag 服务 V1 基线：本服务库里只有 Seata AT 的回滚日志表 undo_log，只在空库上执行；
-- 已有库由 Flyway baseline 登记为版本 1，不执行本文件。undo_log 定义沿用 Apache Seata v2.6.0 PostgreSQL 脚本。

-- Seata AT 回滚日志表。
CREATE TABLE IF NOT EXISTS undo_log (
    id SERIAL NOT NULL,
    branch_id BIGINT NOT NULL,
    xid VARCHAR(128) NOT NULL,
    context VARCHAR(128) NOT NULL,
    rollback_info BYTEA NOT NULL,
    log_status INT NOT NULL,
    log_created TIMESTAMP(6) NOT NULL,
    log_modified TIMESTAMP(6) NOT NULL,
    CONSTRAINT pk_undo_log PRIMARY KEY (id),
    CONSTRAINT ux_undo_log UNIQUE (xid, branch_id)
);
CREATE INDEX IF NOT EXISTS ix_log_created ON undo_log(log_created);
COMMENT ON TABLE undo_log IS 'AT transaction mode undo table';
COMMENT ON COLUMN undo_log.branch_id IS 'branch transaction id';
COMMENT ON COLUMN undo_log.xid IS 'global transaction id';
COMMENT ON COLUMN undo_log.context IS 'undo_log context,such as serialization';
COMMENT ON COLUMN undo_log.rollback_info IS 'rollback info';
COMMENT ON COLUMN undo_log.log_status IS '0:normal status,1:defense status';
COMMENT ON COLUMN undo_log.log_created IS 'create datetime';
COMMENT ON COLUMN undo_log.log_modified IS 'modify datetime';
