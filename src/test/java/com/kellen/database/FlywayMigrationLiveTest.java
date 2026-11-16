package com.kellen.database;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;

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
}
