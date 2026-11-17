package com.kellen.example.entity;

import com.baomidou.mybatisplus.annotation.TableName;
import com.kellen.entity.TenantEntityBase;
import com.kellen.example.entity.enums.ExampleStateEnum;
import io.swagger.v3.oas.annotations.media.Schema;
import lombok.Getter;
import lombok.Setter;

/**
 * 示例业务对象。
 * <p>
 * AI 新增实体时继承 TenantEntityBase，每张表都必须有全部基础字段。
 * 基类已有 id、code、description、审计字段、deleted、label、sorting、乐观锁 version，租户表另有 tenantId、createDept，
 * 实体只声明业务字段；type、state 等用到才由当前实体声明。
 *
 * @author sunkailun
 * @className ExampleEntity
 * @time 2026/05/26
 */
@Getter
@Setter
@TableName("example_record")
@Schema(description = "示例业务对象")
public class ExampleEntity extends TenantEntityBase {

    /**
     * 示例名称。
     */
    @Schema(description = "示例名称")
    private String name;

    /**
     * 示例状态。
     * <p>
     * state 是业务状态字段，必须由当前业务模块定义自己的 IEnum，
     * 不要把业务状态枚举放入实体基类。
     */
    @Schema(description = "示例状态")
    private ExampleStateEnum state;
}
