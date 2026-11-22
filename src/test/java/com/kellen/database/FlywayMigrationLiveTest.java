package com.kellen.database;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

/** 本服务库里只有 Seata AT 的 undo_log：迁移在独占 schema 上实际执行，结果只有 undo_log 与 Flyway 的记录表；不连接真实业务库。 */
@EnabledIfEnvironmentVariable(named = "FLEET_PG_TEST_URL", matches = ".+")
class FlywayMigrationLiveTest extends PostgresqlSchemaFixture {

    @Test
    void migratesToUndoLogOnly() throws Exception {
        assertThat(migrate(null).success).isTrue();
        assertThat(appliedVersions()).containsExactly("1", "2");
        assertThat(strings("SELECT table_name FROM information_schema.tables WHERE table_schema = '" + schema
                + "' AND table_type = 'BASE TABLE' ORDER BY 1")).containsExactly("flyway_schema_history", "undo_log");
        assertThat(migrate(null).migrationsExecuted).isZero();
    }

    /** 每张表都必须有的全部基础字段；undo_log 与 Flyway 记录表是框架表，除外。以后新增业务表时由此把关。 */
    private static final List<String> BASE_COLUMNS = List.of("id", "code", "description", "create_time", "create_by",
            "update_time", "update_by", "is_deleted", "label", "sort", "version", "tenant_id", "create_dept");

    @Test
    void everyTableHasAllBaseColumns() throws Exception {
        migrate(null);
        for (String table : strings("SELECT table_name FROM information_schema.tables WHERE table_schema = '" + schema
                + "' AND table_type = 'BASE TABLE' AND table_name NOT IN ('undo_log', 'flyway_schema_history') ORDER BY 1")) {
            assertThat(columns(table)).as(table).containsAll(BASE_COLUMNS);
        }
    }
}
