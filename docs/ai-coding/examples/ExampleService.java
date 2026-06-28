package com.kellen.example.service;

import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.baomidou.mybatisplus.extension.service.IService;
import com.kellen.example.entity.ExampleEntity;
import com.kellen.example.entity.bo.ExampleBO;
import com.kellen.example.entity.query.ExampleQuery;
import com.kellen.example.entity.vo.ExampleVO;

import java.util.List;

/**
 * 示例业务 Service 服务接口层。
 *
 * @author sunkailun
 * @className ExampleService
 * @time 2026/05/26
 */
public interface ExampleService extends IService<ExampleEntity> {

    /**
     * 分页查询。
     *
     * @param page         分页对象
     * @param exampleQuery 查询参数
     * @return 分页结果
     */
    Page<ExampleVO> pageEnhance(Page<ExampleEntity> page, ExampleQuery exampleQuery);

    /**
     * 集合查询。
     *
     * @param exampleQuery 查询参数
     * @return 列表结果
     */
    List<ExampleVO> listEnhance(ExampleQuery exampleQuery);

    /**
     * 单条查询。
     *
     * @param exampleQuery 查询参数
     * @return 单条结果
     */
    ExampleVO getOneEnhance(ExampleQuery exampleQuery);

    /**
     * 总数查询。
     *
     * @param exampleQuery 查询参数
     * @return 总数
     */
    Long countEnhance(ExampleQuery exampleQuery);

    /**
     * 新增。
     *
     * @param exampleBO 新增参数
     * @return 主键
     */
    String saveEnhance(ExampleBO exampleBO);

    /**
     * 修改。
     *
     * @param exampleBO 修改参数
     * @return 是否成功
     */
    Boolean updateEnhance(ExampleBO exampleBO);

    /**
     * 删除。
     *
     * @param id 示例主键
     * @return 是否成功
     */
    Boolean removeEnhance(String id);
}
