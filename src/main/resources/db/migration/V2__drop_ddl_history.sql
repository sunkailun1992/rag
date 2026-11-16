-- 删除 MyBatis-Plus DDL 建表方式留下的执行记录表；表结构改由 Flyway 的 flyway_schema_history 记录。
-- 空库从 V1 起迁移时没有这张表，直接跳过。
DROP TABLE IF EXISTS ddl_history;
