package com.kellen.rag.client;

import org.junit.jupiter.api.Test;
import tools.jackson.databind.ObjectMapper;
import java.nio.file.Path;
import java.util.List;
import static org.junit.jupiter.api.Assertions.*;

class LexicalSparseVectorTest {
    @Test void shouldNormalizePostUnicode13CompatibilityCharacters() {
        assertEquals(LexicalSparseVector.encode("c f q q"), LexicalSparseVector.encode("\uA7F2 \uA7F3 \uA7F4 \uD801\uDFA5"));
    }
    @Test void shouldMatchPythonGoldenVectors() throws Exception {
        var cases = new ObjectMapper().readTree(Path.of("engine/tests/fixtures/lexical-v1.json").toFile()).path("cases");
        for (var sample : cases) {
            if (sample.path("indices").isEmpty()) {
                assertThrows(IllegalArgumentException.class, () -> LexicalSparseVector.encode(sample.path("text").asText()));
                continue;
            }
            var result = LexicalSparseVector.encode(sample.path("text").asText());
            var indices = (List<?>) result.get("indices");
            var values = (List<?>) result.get("values");
            assertEquals(sample.path("indices").size(), indices.size());
            for (int i = 0; i < indices.size(); i++) {
                assertEquals(sample.path("indices").get(i).asLong(), ((Number) indices.get(i)).longValue());
                assertEquals(sample.path("values").get(i).asDouble(), ((Number) values.get(i)).doubleValue(), 1e-12);
            }
        }
    }
}
