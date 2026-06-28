package com.kellen.example.entity.enums;

import com.baomidou.mybatisplus.annotation.IEnum;
import lombok.AllArgsConstructor;
import lombok.Getter;

/**
 * 示例状态枚举。
 * <p>
 * AI 编写业务枚举时，需要放在当前业务模块的 entity.enums 包下，
 * 并实现 MyBatis-Plus 的 IEnum 接口，保证数据库值和 Java 枚举互相转换。
 *
 * @author sunkailun
 * @className ExampleStateEnum
 * @time 2026/05/26
 */
@Getter
@AllArgsConstructor
public enum ExampleStateEnum implements IEnum<Integer> {

    /**
     * 默认状态。
     */
    默认(0, "默认"),

    /**
     * 启用状态。
     */
    启用(1, "启用"),

    /**
     * 禁用状态。
     */
    禁用(2, "禁用");

    /**
     * 数据库存储值。
     */
    private final Integer value;

    /**
     * 前端展示说明。
     */
    private final String desc;

    /**
     * 通过数据库值获取枚举。
     *
     * @param value 数据库存储值
     * @return 示例状态枚举
     */
    public static ExampleStateEnum getExampleStateEnum(Integer value) {
        // 遍历所有枚举值，找到 value 相同的枚举。
        for (ExampleStateEnum exampleStateEnum : ExampleStateEnum.values()) {
            // 判断数据库值是否匹配。
            if (exampleStateEnum.getValue().equals(value)) {
                // 返回匹配的枚举。
                return exampleStateEnum;
            }
        }
        // 没有匹配值时返回空。
        return null;
    }

    /**
     * 通过数据库值获取说明。
     *
     * @param value 数据库存储值
     * @return 枚举说明
     */
    public static String getDesc(Integer value) {
        // 根据 value 获取枚举。
        ExampleStateEnum exampleStateEnum = getExampleStateEnum(value);
        // 枚举存在时返回说明，否则返回空。
        return exampleStateEnum == null ? null : exampleStateEnum.getDesc();
    }
}
