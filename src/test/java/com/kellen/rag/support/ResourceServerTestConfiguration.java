package com.kellen.rag.support;

import com.kellen.security.oauth2.FleetResourceServerAutoConfiguration;
import com.kellen.security.oauth2.SecurityContextRpcService;
import org.springframework.boot.autoconfigure.ImportAutoConfiguration;
import org.springframework.boot.test.context.TestConfiguration;
import org.springframework.context.annotation.Bean;
import org.springframework.security.oauth2.jwt.BadJwtException;
import org.springframework.security.oauth2.jwt.JwtDecoder;

/**
 * 控制器测试用的资源服务器：照常装 utils 的过滤链与方法级权限，令牌解码一律拒绝（测试直接注入认证结果）。
 * 测试的 properties 要带上 {@code spring.security.oauth2.resourceserver.jwt.issuer-uri}，自动配置按这个属性装配。
 */
@TestConfiguration(proxyBeanMethods = false)
@ImportAutoConfiguration(FleetResourceServerAutoConfiguration.class)
public class ResourceServerTestConfiguration {

    /**
     * 业务上下文提供方占位：资源服务器要求有提供方（utils 2.0.8 起没有就启动失败）；控制器测试直接注入认证结果，
     * 令牌一律被下面的解码器拒绝，走不到这里，走到就按 user 服务不可用（503）暴露出来。
     *
     * @return 提供方
     */
    @Bean
    SecurityContextRpcService securityContextRpcService() {
        return (userId, requestedTenantId) -> {
            throw new IllegalStateException("控制器测试直接注入认证结果，不加载业务上下文");
        };
    }

    @Bean
    JwtDecoder rejectingJwtDecoder() {
        return token -> {
            throw new BadJwtException("Test decoder rejects every token");
        };
    }
}
