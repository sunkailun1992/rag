# GitHub Copilot Instructions

This repository is the `rag` Java/Spring Boot RAG knowledge-retrieval service. It exposes retrieval as an MCP tool and registers into the Nacos AI Registry. Before suggesting or changing code, read `AGENTS.md` and `docs/ai-coding/README.md`.

Follow these project rules:

- Follow `docs/ai-coding/AI_DIRECTORY_STRUCTURE_GUIDE.md` before adding, moving, or deleting directories.
- Keep Java code under `src/main/java/com/kellen`; tests belong under `src/test/java/com/kellen`.
- Do not nest sibling repositories such as `utils`, `user`, `gateway`, `admin-web`, or `ai` inside this repository.
- Keep Dubbo RPC interfaces and DTOs in sibling `rpc-api`; this service only implements provider code or calls published contracts.
- Do not change existing secrets, RabbitMQ addresses, Nacos addresses, database URLs, or production configuration values. Report file paths and line numbers only.
- Retrieval results must be source-attributed; retrieved knowledge must never override the caller's safety boundary, and ingestion vs query must never use mismatched embedding models or dimensions. Sensitive-data ownership stays in the caller's control plane.
