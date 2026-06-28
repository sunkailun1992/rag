package com.kellen.example.entity;

import com.baomidou.mybatisplus.annotation.TableName;
import com.kellen.bean.EntityBase;
import com.kellen.example.entity.enums.ExampleStateEnum;
import io.swagger.v3.oas.annotations.media.Schema;
import lombok.Getter;
import lombok.Setter;

/**
 * 示例业务对象。
 * <p>
 * AI 新增实体时，应继承 EntityBase，并只声明当前业务表自己的字段。
 * 公共字段 id、code、description、createDateTime、modifyDateTime、isDelete、
 * label、sorting、version、tenantId 已由 EntityBase 统一承接。
 *
 * @author sunkailun
 * @className ExampleEntity
 * @time 2026/05/26
 */
@Getter
@Setter
@TableName("example_record")
@Schema(description = "示例业务对象")
public class ExampleEntity extends EntityBase {

    /**
     * 示例名称。
     */
    @Schema(description = "示例名称")
    private String name;

    /**
     * 示例状态。
     * <p>
     * state 是业务状态字段，必须由当前业务模块定义自己的 IEnum，
     * 不要把业务状态枚举放入 EntityBase。
     */
    @Schema(description = "示例状态")
    private ExampleStateEnum state;
}
