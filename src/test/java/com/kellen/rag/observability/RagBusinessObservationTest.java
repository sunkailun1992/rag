package com.kellen.rag.observability;

import com.kellen.rag.client.RagEngineClient;
import com.kellen.rag.config.RagVectorProperties;
import com.kellen.rag.service.impl.DefaultRagRetrievalService;
import com.kellen.rpc.rag.RagRetrievalRpcRequest;
import com.kellen.utils.context.TenantContextHolder;
import io.micrometer.core.instrument.simple.SimpleMeterRegistry;
import org.junit.jupiter.api.Test;
import org.springframework.aop.aspectj.annotation.AspectJProxyFactory;
import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

/** 经真实服务代理验证 HTTP200/RPC 降级仍统计 unavailable，且不把租户放入指标。 */
class RagBusinessObservationTest {
    @Test
    void countsBusinessFailureAndRestoresTenantAfterTest() {
        var registry = new SimpleMeterRegistry();
        var engine = mock(RagEngineClient.class);
        when(engine.retrieve(anyString(), anyString(), anyInt(), anyInt(), anyDouble(), anyInt()))
                .thenThrow(new IllegalStateException("sensitive synthetic exception"));
        var target = new DefaultRagRetrievalService(new RagVectorProperties(), engine);
        var factory = new AspectJProxyFactory(target);
        factory.addAspect(new RagBusinessObservation(registry));
        com.kellen.rag.service.RagRetrievalService proxy = factory.getProxy();
        var request = new RagRetrievalRpcRequest();
        request.setQuery("sensitive synthetic query");
        TenantContextHolder.setTenantId("synthetic-tenant-should-not-be-label");
        try {
            assertThat(proxy.retrieve(request).isMatched()).isFalse();
            assertThat(registry.get("rag.retrieval.count").tag("outcome", "unavailable").counter().count()).isEqualTo(1);
            assertThat(registry.get("rag.retrieval.duration").tag("outcome", "unavailable").timer().count()).isEqualTo(1);
            assertThat(registry.getMeters()).allSatisfy(meter ->
                    assertThat(meter.getId().getTags()).hasSize(1));
        } finally { TenantContextHolder.clear(); }
    }
}
