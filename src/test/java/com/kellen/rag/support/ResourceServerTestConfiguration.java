package com.kellen.rag.support;

import com.kellen.security.oauth2.FleetResourceServerAutoConfiguration;
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

    @Bean
    JwtDecoder rejectingJwtDecoder() {
        return token -> {
            throw new BadJwtException("Test decoder rejects every token");
        };
    }
}
