package com.kellen.example.service.impl;

import com.baomidou.mybatisplus.core.conditions.query.QueryWrapper;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import com.kellen.example.entity.ExampleEntity;
import com.kellen.example.entity.bo.ExampleBO;
import com.kellen.example.entity.query.ExampleQuery;
import com.kellen.example.entity.vo.ExampleVO;
import com.kellen.example.mapper.ExampleMapper;
import com.kellen.example.service.ExampleService;
import com.kellen.example.service.query.ExampleServiceQuery;
import com.kellen.example.service.results.ExampleServiceResults;
import com.kellen.utils.GeneralConvertor;
import org.apache.commons.lang3.StringUtils;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;

/**
 * 示例业务 Service 服务实现层。
 *
 * @author sunkailun
 * @className ExampleServiceImpl
 * @time 2026/05/26
 */
@Service
public class ExampleServiceImpl extends ServiceImpl<ExampleMapper, ExampleEntity> implements ExampleService {

    /**
     * 示例业务 Mapper。
     */
    private final ExampleMapper exampleMapper;

    /**
     * 示例业务查询增强。
     */
    private final ExampleServiceQuery exampleServiceQuery;

    /**
     * 示例业务结果增强。
     */
    private final ExampleServiceResults exampleServiceResults;

    /**
     * 构造示例业务 Service。
     *
     * @param exampleMapper         示例业务 Mapper
     * @param exampleServiceQuery   示例业务查询增强
     * @param exampleServiceResults 示例业务结果增强
     */
    public ExampleServiceImpl(ExampleMapper exampleMapper,
                              ExampleServiceQuery exampleServiceQuery,
                              ExampleServiceResults exampleServiceResults) {
        // 注入 Mapper。
        this.exampleMapper = exampleMapper;
        // 注入查询增强。
        this.exampleServiceQuery = exampleServiceQuery;
        // 注入结果增强。
        this.exampleServiceResults = exampleServiceResults;
    }

    /**
     * 分页查询。
     *
     * @param page         分页对象
     * @param exampleQuery 查询参数
     * @return 分页结果
     */
    @Override
    public Page<ExampleVO> pageEnhance(Page<ExampleEntity> page, ExampleQuery exampleQuery) {
        // 根据 Query 构建完整查询包装器。
        QueryWrapper<ExampleEntity> queryWrapper = buildQueryWrapper(exampleQuery);
        // 执行分页查询。
        Page<ExampleEntity> pageDO = exampleMapper.selectPage(page, queryWrapper);
        // 转换为 VO 分页。
        Page<ExampleVO> pageVO = exampleServiceResults.toPageVO(pageDO);
        // 判断是否需要结果增强。
        return needAssignment(exampleQuery) ? exampleServiceResults.assignment(pageVO) : pageVO;
    }

    /**
     * 集合查询。
     *
     * @param exampleQuery 查询参数
     * @return 列表结果
     */
    @Override
    public List<ExampleVO> listEnhance(ExampleQuery exampleQuery) {
        // 根据 Query 构建完整查询包装器。
        QueryWrapper<ExampleEntity> queryWrapper = buildQueryWrapper(exampleQuery);
        // 执行列表查询。
        List<ExampleEntity> records = exampleMapper.selectList(queryWrapper);
        // 转换为 VO 列表。
        List<ExampleVO> voRecords = exampleServiceResults.toListVO(records);
        // 判断是否需要结果增强。
        return needAssignment(exampleQuery) ? exampleServiceResults.assignment(voRecords) : voRecords;
    }

    /**
     * 单条查询。
     *
     * @param exampleQuery 查询参数
     * @return 单条结果
     */
    @Override
    public ExampleVO getOneEnhance(ExampleQuery exampleQuery) {
        // 根据 Query 构建完整查询包装器。
        QueryWrapper<ExampleEntity> queryWrapper = buildQueryWrapper(exampleQuery);
        // 执行单条查询。
        ExampleEntity record = exampleMapper.selectOne(queryWrapper);
        // 转换为 VO。
        ExampleVO vo = exampleServiceResults.toVO(record);
        // 判断是否需要结果增强。
        return needAssignment(exampleQuery) ? exampleServiceResults.assignment(vo) : vo;
    }

    /**
     * 总数查询。
     *
     * @param exampleQuery 查询参数
     * @return 总数
     */
    @Override
    public Long countEnhance(ExampleQuery exampleQuery) {
        // 根据 Query 构建完整查询包装器。
        QueryWrapper<ExampleEntity> queryWrapper = buildQueryWrapper(exampleQuery);
        // 返回总数。
        return exampleMapper.selectCount(queryWrapper);
    }

    /**
     * 新增。
     *
     * @param exampleBO 新增参数
     * @return 主键
     */
    @Override
    @Transactional(rollbackFor = Exception.class)
    public String saveEnhance(ExampleBO exampleBO) {
        // 将 BO 转换为实体。
        ExampleEntity entity = GeneralConvertor.convertor(exampleBO, ExampleEntity.class);
        // 插入数据。
        exampleMapper.insert(entity);
        // 返回主键。
        return entity.getId();
    }

    /**
     * 修改。
     *
     * @param exampleBO 修改参数
     * @return 是否成功
     */
    @Override
    @Transactional(rollbackFor = Exception.class)
    public Boolean updateEnhance(ExampleBO exampleBO) {
        // 将 BO 转换为实体，保留旧 version 触发乐观锁。
        ExampleEntity entity = GeneralConvertor.convertor(exampleBO, ExampleEntity.class);
        // 使用 MyBatis-Plus 内置 updateById，确保 @Version 乐观锁插件能读取实体中的旧版本号。
        int count = exampleMapper.updateById(entity);
        // 返回是否成功。
        return count > 0;
    }

    /**
     * 删除。
     *
     * @param id 示例主键
     * @return 是否成功
     */
    @Override
    @Transactional(rollbackFor = Exception.class)
    public Boolean removeEnhance(String id) {
        // 创建查询包装器。
        QueryWrapper<ExampleEntity> queryWrapper = new QueryWrapper<>();
        // 根据主键删除。
        queryWrapper.eq("id", id);
        // 执行逻辑删除。
        int count = exampleMapper.delete(queryWrapper);
        // 返回是否成功。
        return count > 0;
    }

    /**
     * 构建查询包装器。
     *
     * @param exampleQuery 查询参数
     * @return 查询包装器
     * @author sunkailun
     */
    private QueryWrapper<ExampleEntity> buildQueryWrapper(ExampleQuery exampleQuery) {
        // 将查询参数转换为实体，用于 QueryWrapper 自动拼接同名字段等值条件。
        ExampleEntity entity = GeneralConvertor.convertor(exampleQuery, ExampleEntity.class);
        // 创建查询包装器。
        QueryWrapper<ExampleEntity> queryWrapper = entity == null ? new QueryWrapper<>() : new QueryWrapper<>(entity);
        // 拼接自动查询条件。
        exampleServiceQuery.query(exampleQuery, queryWrapper);
        // 拼接人工查询条件。
        queryArtificial(exampleQuery, queryWrapper);
        // 返回完整查询包装器。
        return queryWrapper;
    }

    /**
     * 人工查询条件。
     *
     * @param exampleQuery 查询参数
     * @param queryWrapper 查询包装器
     * @return 查询包装器
     * @author sunkailun
     */
    private QueryWrapper<ExampleEntity> queryArtificial(ExampleQuery exampleQuery, QueryWrapper<ExampleEntity> queryWrapper) {
        // 业务特殊查询条件统一写在这里。
        // 查询对象为空时直接返回原包装器。
        if (exampleQuery == null) {
            // 返回调用方传入的 QueryWrapper。
            return queryWrapper;
        }
        // 通用关键字为空时不拼接人工查询条件。
        if (StringUtils.isBlank(exampleQuery.getQuery())) {
            // 返回调用方传入的 QueryWrapper。
            return queryWrapper;
        }
        // 将通用关键字拼接到业务允许模糊查询的字段上。
        queryWrapper.and(wrapper -> wrapper.like("name", exampleQuery.getQuery()));
        return queryWrapper;
    }

    /**
     * 判断是否需要结果增强。
     *
     * @param exampleQuery 查询参数
     * @return boolean
     * @author sunkailun
     */
    private boolean needAssignment(ExampleQuery exampleQuery) {
        // 查询对象为空时默认执行结果增强。
        if (exampleQuery == null) {
            // 返回需要增强。
            return true;
        }
        // assignment 明确传 false 时跳过结果增强，其余情况默认增强。
        return !Boolean.FALSE.equals(exampleQuery.getAssignment());
    }
}
