package com.kellen.bean;

import com.baomidou.mybatisplus.extension.ddl.IDdl;
import org.springframework.stereotype.Component;

import javax.sql.DataSource;
import java.util.List;
import java.util.function.Consumer;

/**
 * MyBatis-Plus自动维护DDL。
 * <p>
 * rag 业务表使用独立增量脚本维护，避免回改已经在目标环境执行过的公共脚本。
 *
 * @author sunkailun
 * @className MysqlDdl
 */
@Component
public class MysqlDdl implements IDdl {

    /**
     * 当前项目数据源。
     */
    private final DataSource dataSource;

    /**
     * 构造MyBatis-Plus自动维护DDL组件。
     *
     * @param dataSource 当前项目数据源
     * @return void
     * @author sunkailun
     */
    public MysqlDdl(DataSource dataSource) {
        this.dataSource = dataSource; // 保存当前应用数据源，交给MyBatis-Plus DDL运行器执行脚本。
    }

    /**
     * 指定执行脚本的数据源。
     *
     * @param consumer MyBatis-Plus DDL脚本执行器
     * @return void
     * @author sunkailun
     */
    @Override
    public void runScript(Consumer<DataSource> consumer) {
        consumer.accept(dataSource); // 使用当前应用数据源执行DDL脚本，避免业务代码手写建表SQL。
    }

    /**
     * 获取自动维护DDL脚本列表。
     *
     * @return java.util.List<java.lang.String>
     * @author sunkailun
     */
    @Override
    public List<String> getSqlFiles() {
        return List.of(
                "db/rag-knowledge-graph-schema.sql"
        ); // 图谱是可重建的派生索引，但仍通过受控 DDL 建表，不在业务请求中动态建表。
    }
}
