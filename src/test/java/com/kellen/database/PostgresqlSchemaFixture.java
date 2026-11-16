package com.kellen.database;

import org.flywaydb.core.Flyway;
import org.flywaydb.core.api.output.MigrateResult;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.springframework.core.io.Resource;
import org.springframework.jdbc.datasource.init.ScriptUtils;

import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.SQLException;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;
import java.util.UUID;
import java.util.Properties;

import static org.assertj.core.api.Assertions.assertThat;

/** 仅允许独立 PostgreSQL 18 测试库；所有业务 fixture 位于每测试独占 schema。 */
abstract class PostgresqlSchemaFixture {
    protected Connection connection;
    protected String schema;

    @BeforeEach
    void createIsolatedSchema() throws Exception {
        Properties properties = new Properties();
        properties.setProperty("user", System.getenv("FLEET_PG_TEST_USERNAME"));
        properties.setProperty("password", System.getenv("FLEET_PG_TEST_PASSWORD"));
        properties.setProperty("connectTimeout", "3");
        properties.setProperty("socketTimeout", "5");
        connection = DriverManager.getConnection(System.getenv("FLEET_PG_TEST_URL"), properties);
        try (var statement = connection.createStatement(); var result = statement.executeQuery("SELECT current_database(), current_setting('server_version_num')::int")) {
            result.next();
            // 先守卫库名，再执行任何 DDL；禁止把业务库当测试库。
            assertThat(result.getString(1)).startsWith("fleet_pg_test_");
            assertThat(result.getInt(2)).isGreaterThanOrEqualTo(180000);
        }
        schema = "fixture_" + UUID.randomUUID().toString().replace("-", "");
        execute("CREATE SCHEMA " + schema);
        execute("SET search_path TO " + schema + ", public");
    }

    @AfterEach
    void removeOnlyOwnSchema() throws Exception {
        if (connection != null) {
            try {
                if (schema != null) {
                    execute("DROP SCHEMA " + schema + " CASCADE");
                }
            } finally {
                connection.close();
            }
        }
    }

    /** 在一个事务里执行一段 SQL 文本，例如从 V1 截出的种子段。 */
    protected void script(Resource resource) throws SQLException {
        // 一个事务执行整段，避免数百条语句逐条提交放大 NAS WAL 同步成本；所有语句仍实际执行。
        if (!connection.getAutoCommit()) {
            throw new IllegalStateException("fixture requires autocommit outside DDL");
        }
        connection.setAutoCommit(false);
        try {
            ScriptUtils.executeSqlScript(connection, resource);
            connection.commit();
        } catch (RuntimeException | SQLException failure) {
            connection.rollback();
            throw failure;
        } finally {
            connection.setAutoCommit(true);
        }
    }

    /**
     * 在本测试独占的 schema 上执行服务自己的 Flyway 迁移。
     *
     * @param target 迁到的版本；为 null 时迁到最新
     * @return Flyway 迁移结果
     */
    protected MigrateResult migrate(String target) {
        var configuration = Flyway.configure()
                .dataSource(System.getenv("FLEET_PG_TEST_URL"), System.getenv("FLEET_PG_TEST_USERNAME"),
                        System.getenv("FLEET_PG_TEST_PASSWORD"))
                .schemas(schema).defaultSchema(schema)
                .locations("classpath:db/migration")
                .cleanDisabled(true);
        if (target != null) {
            configuration.target(target);
        }
        return configuration.load().migrate();
    }

    /** 已成功应用的迁移版本，按执行顺序。 */
    protected List<String> appliedVersions() throws SQLException {
        return strings("SELECT version FROM " + schema + ".flyway_schema_history WHERE success AND version IS NOT NULL ORDER BY installed_rank");
    }

    /** 本测试 schema 里某张表的全部列名。 */
    protected Set<String> columns(String table) throws SQLException {
        return new LinkedHashSet<>(strings("SELECT column_name FROM information_schema.columns WHERE table_schema = '"
                + schema + "' AND table_name = '" + table + "' ORDER BY ordinal_position"));
    }

    protected List<String> strings(String sql) throws SQLException {
        List<String> values = new ArrayList<>();
        try (var statement = connection.createStatement(); var result = statement.executeQuery(sql)) {
            while (result.next()) {
                values.add(result.getString(1));
            }
        }
        return values;
    }

    protected void execute(String sql) throws SQLException {
        try (var statement = connection.createStatement()) {
            statement.execute(sql);
        }
    }

    protected long number(String sql) throws SQLException {
        try (var statement = connection.createStatement(); var result = statement.executeQuery(sql)) {
            result.next();
            return result.getLong(1);
        }
    }
}
