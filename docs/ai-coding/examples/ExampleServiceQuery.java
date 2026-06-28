package com.kellen.example.service.query;

import com.baomidou.mybatisplus.core.conditions.query.QueryWrapper;
import com.kellen.example.entity.ExampleEntity;
import com.kellen.example.entity.query.ExampleQuery;
import org.apache.commons.lang3.StringUtils;
import org.springframework.stereotype.Service;

import java.util.Arrays;
import java.util.Map;

/**
 * 示例业务 Service 查询增强层。
 * <p>
 * 自动查询条件统一放在该类，ServiceImpl 只负责调用，不直接堆查询细节。
 *
 * @author sunkailun
 * @className ExampleServiceQuery
 * @time 2026/05/26
 */
@Service
public class ExampleServiceQuery {

    /**
     * 默认排序字段。
     */
    private static final String DEFAULT_SORT_FIELD = "create_date_time";

    /**
     * 允许查询和排序的字段白名单。
     */
    private static final Map<String, String> ALLOWED_FIELDS = Map.ofEntries(
            Map.entry("id", "id"), // 允许按主键查询和排序。
            Map.entry("code", "code"), // 允许按业务编码查询和排序。
            Map.entry("name", "name"), // 允许按业务名称查询和排序。
            Map.entry("state", "state"), // 允许按业务状态查询和排序。
            Map.entry("tenantId", "tenant_id"), // 允许按租户ID查询和排序。
            Map.entry("tenant_id", "tenant_id"), // 兼容前端传数据库列名。
            Map.entry("sorting", "sorting"), // 允许按排序值查询和排序。
            Map.entry("version", "version"), // 允许查询乐观锁版本号。
            Map.entry("createDateTime", "create_date_time"), // 允许按创建时间查询和排序。
            Map.entry("create_date_time", "create_date_time"), // 兼容前端传数据库列名。
            Map.entry("modifyDateTime", "modify_date_time"), // 允许按修改时间查询和排序。
            Map.entry("modify_date_time", "modify_date_time") // 兼容前端传数据库列名。
    );

    /**
     * 构建查询条件。
     * <p>
     * QueryWrapper 已经通过 new QueryWrapper<>(entity) 承接 Entity 同名字段等值查询；
     * 本方法只补充排序、显示字段等无法由 Entity 自动表达的通用查询条件。
     *
     * @param exampleQuery 查询参数
     * @param queryWrapper 查询包装器
     * @return 查询包装器
     * @author sunkailun
     */
    public QueryWrapper<ExampleEntity> query(ExampleQuery exampleQuery, QueryWrapper<ExampleEntity> queryWrapper) {
        // 查询对象为空时直接返回原包装器，避免示例生成空指针代码。
        if (exampleQuery == null) {
            // 返回调用方传入的 QueryWrapper。
            return queryWrapper;
        }

        // 计算白名单内排序字段，非法字段回退到默认创建时间字段。
        String sortField = resolveSortField(exampleQuery.getCollationFields());
        // 判断是否显式要求升序。
        if (Boolean.TRUE.equals(exampleQuery.getCollation())) {
            // 拼接升序排序。
            queryWrapper.orderByAsc(sortField);
        } else {
            // 未传排序方向或传 false 时统一按降序排序。
            queryWrapper.orderByDesc(sortField);
        }

        // 解析白名单内显示字段，非法字段不会进入 SQL。
        String[] selectFields = resolveSelectFields(exampleQuery.getFields());
        // 有合法显示字段时才拼接 select。
        if (selectFields.length > 0) {
            // 指定白名单校验后的 select 字段。
            queryWrapper.select(selectFields);
        }

        // 返回查询包装器。
        return queryWrapper;
    }

    /**
     * 解析安全排序字段。
     *
     * @param requestedField 前端请求排序字段
     * @return 安全排序字段
     * @author sunkailun
     */
    private String resolveSortField(String requestedField) {
        // 读取请求排序字段对应的数据库列名。
        String requestedColumn = ALLOWED_FIELDS.get(StringUtils.trimToEmpty(requestedField));
        // 请求字段命中白名单时直接返回白名单列名。
        if (StringUtils.isNotBlank(requestedColumn)) {
            // 返回白名单列名，避免原始请求字段进入 SQL。
            return requestedColumn;
        }
        // 返回默认排序字段对应的白名单列名。
        return ALLOWED_FIELDS.get(DEFAULT_SORT_FIELD);
    }

    /**
     * 解析安全查询字段。
     *
     * @param requestedFields 前端请求显示字段
     * @return 安全查询字段
     * @author sunkailun
     */
    private String[] resolveSelectFields(String requestedFields) {
        // 未指定显示字段时返回空数组，由 MyBatis-Plus 执行默认 select。
        if (StringUtils.isBlank(requestedFields)) {
            // 返回空数组表示不拼接自定义显示字段。
            return new String[0];
        }
        // 按英文逗号拆分字段，并映射到白名单数据库列。
        return Arrays.stream(requestedFields.split(","))
                // 去掉字段两侧空白，避免空格影响白名单匹配。
                .map(StringUtils::trimToEmpty)
                // 将请求字段转换成白名单内数据库列名。
                .map(ALLOWED_FIELDS::get)
                // 过滤未命中白名单的非法字段。
                .filter(StringUtils::isNotBlank)
                // 去重，避免重复 select 同一字段。
                .distinct()
                // 转换为 QueryWrapper 支持的字段数组。
                .toArray(String[]::new);
    }
}
