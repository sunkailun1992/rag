package com.kellen.example.entity.bo;

import com.kellen.example.entity.enums.ExampleStateEnum;
import io.swagger.v3.oas.annotations.media.Schema;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import lombok.Data;

import java.io.Serializable;

/**
 * 示例业务写入参数。
 * <p>
 * 简单 CRUD 可以使用一个 BO 配合 Save、Update 校验分组；标准删除使用 RESTful 路径 id，不需要删除 BO。
 *
 * @author sunkailun
 * @className ExampleBO
 * @time 2026/05/26
 */
@Data
@Schema(description = "示例业务写入参数")
public class ExampleBO implements Serializable {

    /**
     * 主键。
     */
    @Schema(description = "主键")
    private String id;

    /**
     * 数据库版本号。
     * <p>
     * 修改时必须提交查询得到的旧版本号，供 MyBatis-Plus @Version 乐观锁判断并发覆盖。
     */
    @Schema(description = "数据库版本号，修改时必传")
    @NotNull(groups = {Update.class}, message = "version不能为空")
    private Integer version;

    /**
     * 示例名称。
     */
    @Schema(description = "示例名称")
    @NotBlank(groups = {Save.class}, message = "name不能为空")
    private String name;

    /**
     * 示例状态。
     */
    @Schema(description = "示例状态")
    private ExampleStateEnum state;

    /**
     * 创建人。
     */
    @Schema(description = "创建人")
    private String createName;

    /**
     * 修改人。
     */
    @Schema(description = "修改人")
    private String modifyName;

    /**
     * 新增校验分组。
     */
    public interface Save {
    }

    /**
     * 修改校验分组。
     */
    public interface Update {
    }
}
