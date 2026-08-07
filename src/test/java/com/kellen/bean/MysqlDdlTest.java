package com.kellen.bean;

import org.junit.jupiter.api.Test;
import org.springframework.core.io.ClassPathResource;

import javax.sql.DataSource;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;

class MysqlDdlTest {

    @Test
    void shouldRegisterKnowledgeGraphSchema() {
        MysqlDdl mysqlDdl = new MysqlDdl(mock(DataSource.class));

        assertThat(mysqlDdl.getSqlFiles())
                .containsExactly("db/rag-knowledge-graph-schema.sql");
    }

    @Test
    void shouldIncludeTenantInGraphUniqueIndexes() throws Exception {
        String sql = new ClassPathResource("db/rag-knowledge-graph-schema.sql")
                .getContentAsString(java.nio.charset.StandardCharsets.UTF_8);

        assertThat(sql)
                .contains("uk_rag_graph_node_tenant_base_code (tenant_id, knowledge_base, code)")
                .contains("uk_rag_graph_edge_tenant_base_code (tenant_id, knowledge_base, code)");
    }
}
