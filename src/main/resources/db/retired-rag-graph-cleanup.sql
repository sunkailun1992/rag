-- 人工审查候选：仅旧 MySQL 派生图索引，不属于应用自动 migration。
-- 执行前：确认目标个人 RAG schema、全量备份、旧版本消费者停止，核对表结构/引用/行数。
-- 先按 docs/legacy-rag-retirement.md 验证 Engine/Neo4j 来源隔离、构建与删除屏障。
-- 不提供 USE；调用者须显式选择经核准的 schema。禁止执行于其它业务库。
-- 本批没有执行此脚本；DROP 无事务回滚，回退旧制品前须先从备份恢复两表。
DROP TABLE IF EXISTS rag_graph_edge;
DROP TABLE IF EXISTS rag_graph_node;
