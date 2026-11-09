package com.kellen.bean;

import com.baomidou.mybatisplus.extension.ddl.IDdl;
import org.springframework.stereotype.Component;

import javax.sql.DataSource;
import java.util.List;
import java.util.function.Consumer;

/**
 * RAG PostgreSQL 公共基础表入口，不恢复已退役的旧知识图谱业务表。
 *
 * <p>Seata AT 基础表仍须在服务首次启动前执行，运行时 DDL 只作幂等校验。</p>
 */
@Component
public class PostgresqlDdl implements IDdl {

    private final DataSource dataSource;

    /** 指定当前服务数据源。 */
    public PostgresqlDdl(DataSource dataSource) {
        this.dataSource = dataSource;
    }

    /** 将数据源交给 MyBatis-Plus 脚本执行器。 */
    @Override
    public void runScript(Consumer<DataSource> consumer) {
        consumer.accept(dataSource);
    }

    /** 仅维护当前架构仍需要的 PostgreSQL 公共表。 */
    @Override
    public List<String> getSqlFiles() {
        return List.of("db/postgresql/common-infra-schema.sql");
    }
}
