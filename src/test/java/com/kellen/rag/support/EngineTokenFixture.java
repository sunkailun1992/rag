package com.kellen.rag.support;

import com.kellen.security.oauth2.client.FleetOAuth2Clients;
import org.springframework.security.oauth2.client.OAuth2AuthorizedClient;
import org.springframework.security.oauth2.client.OAuth2AuthorizedClientManager;
import org.springframework.security.oauth2.client.registration.ClientRegistration;
import org.springframework.security.oauth2.client.registration.InMemoryClientRegistrationRepository;
import org.springframework.security.oauth2.core.AuthorizationGrantType;
import org.springframework.security.oauth2.core.OAuth2AccessToken;

import java.time.Instant;
import java.util.function.Supplier;

/**
 * 不测取令牌本身的引擎契约用例用：{@code github-rag-engine} 注册直接给出固定的客户端凭证令牌（或取不到令牌）。
 * 取令牌的真实流程（令牌端点、失败不调引擎）见 {@code RagEngineClientAuthTest}。
 */
public final class EngineTokenFixture {

    /**
     * 固定令牌原文。
     */
    public static final String TOKEN = "fixture-engine-client-credentials-token";

    private EngineTokenFixture() {
    }

    /**
     * 总能取到 {@link #TOKEN} 的出站工具。
     *
     * @return 出站工具
     */
    public static FleetOAuth2Clients fixedToken() {
        return from(() -> TOKEN);
    }

    /**
     * 取不到令牌的出站工具（模拟 Keycloak 不可用）。
     *
     * @return 出站工具
     */
    public static FleetOAuth2Clients unavailable() {
        return from(() -> null);
    }

    /**
     * 每次取令牌时读一次给定来源的出站工具；来源给 null 表示取不到令牌。
     *
     * @param token 令牌来源
     * @return 出站工具
     */
    public static FleetOAuth2Clients from(Supplier<String> token) {
        ClientRegistration registration = ClientRegistration.withRegistrationId("github-rag-engine")
                .clientId("github-rag").clientSecret("fixture-secret")
                .authorizationGrantType(AuthorizationGrantType.CLIENT_CREDENTIALS)
                .tokenUri("https://keycloak.invalid/realms/github-dev/protocol/openid-connect/token").build();
        OAuth2AuthorizedClientManager manager = request -> {
            String value = token.get();
            return value == null ? null : new OAuth2AuthorizedClient(registration, request.getPrincipal().getName(),
                    new OAuth2AccessToken(OAuth2AccessToken.TokenType.BEARER, value, Instant.now(), Instant.now().plusSeconds(300)));
        };
        return new FleetOAuth2Clients(manager, new InMemoryClientRegistrationRepository(registration));
    }
}
