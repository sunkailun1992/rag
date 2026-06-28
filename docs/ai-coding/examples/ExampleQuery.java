package com.kellen.example.entity.query;

import com.kellen.example.entity.enums.ExampleStateEnum;
import io.swagger.v3.oas.annotations.media.Schema;
import jakarta.validation.constraints.Min;
import lombok.Data;

import java.io.Serializable;

/**
 * 示例业务查询对象。
 * <p>
 * Query 用于分页、列表、单条、总数等查询入参。
 * GET 列表、分页和统计查询由 Controller 使用 @ParameterObject 展开为 URL 查询参数；
 * 普通查询和分页统一使用 GET URL 参数，避免为常规查询额外设计 POST 搜索接口。
 * 与 Entity 同名的字段会先转换成 Entity，再由 QueryWrapper 自动拼接等值条件；
 * 不属于 Entity 的分页、排序、显示字段和关键字条件，由 ServiceQuery 或人工查询方法处理。
 *
 * @author sunkailun
 * @className ExampleQuery
 * @time 2026/05/26
 */
@Data
@Schema(description = "示例业务查询对象")
public class ExampleQuery implements Serializable {

    /**
     * 主键。
     * <p>
     * 该字段会通过 GeneralConvertor 转换到 ExampleEntity.id，用于 QueryWrapper 自动等值查询。
     */
    @Schema(description = "主键", example = "1000000000000000001")
    private String id;

    /**
     * 示例名称。
     * <p>
     * 该字段会通过 GeneralConvertor 转换到 ExampleEntity.name，用于 QueryWrapper 自动等值查询。
     */
    @Schema(description = "示例名称", example = "示例名称")
    private String name;

    /**
     * 示例状态。
     * <p>
     * 该字段会通过 GeneralConvertor 转换到 ExampleEntity.state，用于状态等值查询。
     */
    @Schema(description = "示例状态", example = "启用")
    private ExampleStateEnum state;

    /**
     * 是否执行结果增强。
     */
    @Schema(description = "是否执行结果增强", example = "true")
    private Boolean assignment;

    /**
     * 显示字段。
     * <p>
     * 该字段不是 Entity 字段，只用于 ServiceQuery 中控制 select 字段。
     */
    @Schema(description = "显示字段", example = "id,name,state,version")
    private String fields;

    /**
     * 排序规则，true 为升序，false 为降序。
     * <p>
     * 该字段不是 Entity 字段，只用于 ServiceQuery 中控制排序方向。
     */
    @Schema(description = "排序规则，true为升序，false为降序", example = "false")
    private Boolean collation;

    /**
     * 排序字段。
     * <p>
     * 该字段不是 Entity 字段，只用于 ServiceQuery 中控制排序字段。
     */
    @Schema(description = "排序字段", example = "create_date_time")
    private String collationFields = "create_date_time";

    /**
     * 模糊查询关键字。
     * <p>
     * 该字段不是 Entity 字段，只用于 queryArtificial 或 ServiceQuery 中拼接人工查询条件。
     */
    @Schema(description = "模糊查询关键字", example = "示例")
    private String query;

    /**
     * 当前页。
     * <p>
     * 分页接口通过 Controller 的 `@GetMapping(params = {"current", "size"})` 保证分页参数存在；
     * 这里不要加 `@NotNull(groups = Select.class)`，否则 OpenAPI 会忽略校验分组并把 options 接口也标记为必填。
     */
    @Schema(description = "当前页", example = "1")
    @Min(groups = {Select.class}, value = 1, message = "current最小为1")
    private Integer current;

    /**
     * 分页显示数量。
     * <p>
     * 分页接口通过 Controller 的 `@GetMapping(params = {"current", "size"})` 保证分页参数存在；
     * 这里只校验取值范围，避免非分页 options 接口被 OpenAPI 错误标记为必填。
     */
    @Schema(description = "分页显示数量", example = "10")
    @Min(groups = {Select.class}, value = 1, message = "size最小为1")
    private Integer size;

    /**
     * 分页查询校验分组。
     */
    public interface Select {
    }

    /**
     * 列表查询校验分组。
     */
    public interface SelectList {
    }

    /**
     * 单条查询校验分组。
     */
    public interface SelectOne {
    }

    /**
     * 总数查询校验分组。
     */
    public interface Count {
    }
}
