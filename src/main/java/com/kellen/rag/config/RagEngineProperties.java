package com.kellen.rag.config;

import lombok.Getter;
import lombok.Setter;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.stereotype.Component;

/** Python 引擎的独立迁移开关；关闭时保持原有 Java 链路。 */
@Getter
@Setter
@Component
@ConfigurationProperties(prefix = "rag.engine")
public class RagEngineProperties {
    /** PDF/图片解析开关；Office 继续使用 Java 结构化解析。 */
    private boolean parseEnabled;
    /** 检索切换开关；必须完成评估后显式启用。 */
    private boolean retrievalEnabled;
    /** Neo4j 派生图开关；缺省关闭时沿用 MySQL 结构投影。 */
    private boolean graphEnabled;
    /** 提交构建、查询与删除屏障的 HTTP 总时限，毫秒；不复用检索预算。 */
    private int graphTimeoutMillis = 10000;
    /** 构建状态查询的 HTTP 总时限，毫秒。 */
    private int graphStatusTimeoutMillis = 3500;
    /** 服务端可信配置的 HTTP(S) 根地址，不能来自终端用户。 */
    private String baseUrl;
    /** Java 门面与引擎之间的服务凭据，不是用户 JWT。 */
    private String apiKey;
    /** 解析 HTTP 总超时，毫秒。 */
    private int parseTimeoutMillis = 60000;
    /** 检索 HTTP 总超时，最多 3500 毫秒，预留上游 5 秒预算。 */
    private int retrievalTimeoutMillis = 3500;
    /** 引擎响应上限，字节，防止异常响应耗尽 Java 堆。 */
    private int maxResponseBytes = 16 * 1024 * 1024;
    /** 单文档最大返回切片数；超过则整体拒绝，不截断后伪称成功。 */
    private int maxChunks = 1000;
}
