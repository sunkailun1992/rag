package com.kellen.example.entity.vo;

import com.kellen.example.entity.enums.ExampleStateEnum;
import io.swagger.v3.oas.annotations.media.Schema;
import lombok.Data;

import java.io.Serializable;

/**
 * 示例业务渲染对象。
 * <p>
 * VO 用于响应前端，不直接暴露不需要展示的数据库字段。
 *
 * @author sunkailun
 * @className ExampleVO
 * @time 2026/05/26
 */
@Data
@Schema(description = "示例业务渲染对象")
public class ExampleVO implements Serializable {

    /**
     * 主键。
     */
    @Schema(description = "主键")
    private String id;

    /**
     * 数据库版本号。
     * <p>
     * 前端修改前需要把该值原样提交回来，保证 MyBatis-Plus 乐观锁能使用旧版本号更新。
     */
    @Schema(description = "数据库版本号")
    private Integer version;

    /**
     * 示例名称。
     */
    @Schema(description = "示例名称")
    private String name;

    /**
     * 示例状态。
     */
    @Schema(description = "示例状态")
    private ExampleStateEnum state;

    /**
     * 示例状态说明。
     */
    @Schema(description = "示例状态说明")
    private String stateDesc;
}
