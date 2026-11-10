# RAG LiteLLM 协议与向量完整性实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 显式Cohere/LiteLLM rerank适配，严格拒绝不完整embedding，保留原内部合同与业务语义。

**Architecture:** 在既有Settings/HTTP调用边界选择端点和wire，Java/Python消费边界先完整验证再允许后续召回或写入；不引入SDK或中间件。

**Tech Stack:** Java17/Spring Boot4/JDK HttpServer、Python3.12/FastAPI/HTTPX/pytest。

**Spec:** `docs/superpowers/specs/2026-11-10-litellm-rag-wire.md`；主会话已明确授权本会话执行，交diff/fulltests供独立review，不额外停等审批。

## Global Constraints

- Java1.2.5/Engine0.2.9本批一次；不提交推送，不读key/Nacos，不启停，不真实付费请求。
- 模型、默认1024维、阈值、缓存、提取与图谱语义均保持；所有HTTP测试只用合成loopback替身。
- 旧dashscope缺省派生保持；cohere显式完整endpoint，失败typed且不自动重试/泄露异常正文。

## Review Focus

1. 缺/重复/越界/非整数index不能错配切片；HTTP参数化反例覆盖。
2. 非数/NaN/Inf/Float32溢出、数量/维度错误整批拒绝；最后一批失败无upsert。
3. endpoint完整路径不能再拼reranks，cohere缺endpoint配置拒绝；旧wire不变。
4. 503/坏ranking不自动重试或变成功；真实HTTP请求计数与degraded/error断言。
5. 原始响应/端点/异常不进入公开失败或日志；合成sentinel断言及现有隐私回归。

## Task1：真实HTTP RED与最小修复

- [x] 新增Spring `QwenEmbeddingContractTest`，真实客户端/摄取Bean+HTTP替身；旧实现19项有10项RED，另原JSON异常cause为RED，修复后20/20通过；末批错误时无upsert。
- [x] 新增Python `test_model_wire_http.py`，真实loopback请求，旧派生/新显式wire、失败计数和embedding边界9项RED后23/23 GREEN。
- [x] Settings新增rerank_endpoint/rerank_wire，retrieval按声明调用并保留内部results守卫；旧正常fixtures补index0。

## Task2：版本、文档与完整交付

- [x] bump Java1.2.5/Engine0.2.9，更新项目metadata/锁文件中的项目版本，不升级依赖；uv lock仅项目版本变化，OpenAPI生成比对无差异，API版本仍1.0.0。
- [x] README、Engine README/.env.example及upgrade说明参数默认/兼容/失败/部署边界。
- [x] Java115/115与bootJar通过；Python580通过/9个live跳过；ruff全src/tests/scripts通过；evaluation总29（20通过/9跳过）；隔离真实Java↔Python合同4/4；secret/diff通过。
- [x] 主会话和独立worker只读review无阻断；主会话已授权正常main提交推送与精确SHA CI，NAS切流/真实有限验证仍由主会话负责。

bootJar沿用仓库固定输出名rag.jar。本次已产出独立只读0400候选并保存SHA回执，通知主会话潜在可变Jar运行风险；不接管运行JVM，不再覆盖此构建输出。最终main/CI和镜像发布证据保存在ignored交付回执，不能以本机测试代替。
