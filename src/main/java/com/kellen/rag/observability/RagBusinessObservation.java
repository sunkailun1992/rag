package com.kellen.rag.observability;

import com.kellen.rag.exception.ExternalServiceException;
import io.micrometer.core.instrument.MeterRegistry;
import io.opentelemetry.api.GlobalOpenTelemetry;
import io.opentelemetry.api.trace.StatusCode;
import org.aspectj.lang.ProceedingJoinPoint;
import org.aspectj.lang.annotation.Around;
import org.aspectj.lang.annotation.Aspect;
import org.springframework.stereotype.Component;
import java.util.concurrent.TimeUnit;

/** 只记录有限业务结果；不读取请求、正文、业务 ID 或原始异常。 */
@Aspect
@Component
public class RagBusinessObservation {
    private final MeterRegistry registry;
    public RagBusinessObservation(MeterRegistry registry) { this.registry = registry; }

    @Around("execution(* com.kellen.rag.service.impl.DefaultRagRetrievalService.retrieve(..))"
            + " || execution(* com.kellen.rag.service.impl.DefaultRagRetrievalService.retrieveCanary(..))")
    public Object observe(ProceedingJoinPoint invocation) throws Throwable {
        long started = System.nanoTime();
        var span = GlobalOpenTelemetry.getTracer("fleet.business").spanBuilder("rag.retrieval").startSpan();
        String outcome = "error";
        try (var scope = span.makeCurrent()) {
            Object result = invocation.proceed();
            var response = (com.kellen.rpc.rag.RagRetrievalRpcDTO) result;
            outcome = !response.isEnabled() ? "disabled" : response.isMatched() ? "matched"
                    : "RAG retrieval is temporarily unavailable".equals(response.getMessage()) ? "unavailable"
                    : "no knowledge matched".equals(response.getMessage()) ? "unmatched" : "rejected";
            return result;
        } catch (ExternalServiceException unavailable) {
            outcome = "unavailable";
            throw unavailable;
        } finally {
            span.setAttribute("business.outcome", outcome);
            if (java.util.Set.of("error", "unavailable", "business_error", "protocol_error").contains(outcome))
                span.setStatus(StatusCode.ERROR);
            span.end();
            registry.counter("rag.retrieval.count", "outcome", outcome).increment();
            registry.timer("rag.retrieval.duration", "outcome", outcome)
                    .record(Math.max(0, System.nanoTime() - started), TimeUnit.NANOSECONDS);
        }
    }
}
