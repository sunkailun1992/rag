package com.kellen.rag.client;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.text.Normalizer;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.TreeMap;
import java.util.regex.Pattern;

/** lexical-v1：与 Python 相同的无模型中文字/双字及英文词 TF 编码。IDF 由 Qdrant 维护。 */
public final class LexicalSparseVector {
    public static final String MODEL = "lexical-v1";
    private static final Pattern WORDS = Pattern.compile("[a-z0-9]+|[\\u3400-\\u4dbf\\u4e00-\\u9fff]+");
    private LexicalSparseVector() { }

    public static Map<String, Object> encode(String content) {
        // Java 17 的 Unicode 13 与 Python 3.12 的 Unicode 15 在这四个兼容字符上不同。
        String compatible = content.replace("\uA7F2", "C").replace("\uA7F3", "F")
                .replace("\uA7F4", "Q").replace("\uD801\uDFA5", "q");
        var matcher = WORDS.matcher(Normalizer.normalize(compatible, Normalizer.Form.NFKC).toLowerCase(Locale.ROOT));
        Map<Long, Integer> counts = new TreeMap<>();
        while (matcher.find()) {
            String value = matcher.group();
            if (value.charAt(0) < 128) add(counts, "w:" + value);
            else {
                for (int i = 0; i < value.length(); i++) {
                    add(counts, "c:" + value.charAt(i));
                    if (i + 1 < value.length()) add(counts, "b:" + value.substring(i, i + 2));
                }
            }
        }
        if (counts.isEmpty()) throw new IllegalArgumentException("切片不包含可建立关键词索引的文字");
        List<Double> weights = counts.values().stream().map(count -> 1D + Math.log(count)).toList();
        double norm = Math.sqrt(weights.stream().mapToDouble(x -> x * x).sum());
        return Map.of("indices", new ArrayList<>(counts.keySet()), "values", weights.stream().map(x -> x / norm).toList());
    }

    private static void add(Map<Long, Integer> counts, String token) {
        try {
            byte[] hash = MessageDigest.getInstance("SHA-256").digest(token.getBytes(StandardCharsets.UTF_8));
            long index = ((hash[0] & 255L) << 24) | ((hash[1] & 255L) << 16)
                    | ((hash[2] & 255L) << 8) | (hash[3] & 255L);
            counts.merge(index, 1, Integer::sum);
        } catch (java.security.NoSuchAlgorithmException exception) {
            throw new IllegalStateException("SHA-256 不可用", exception);
        }
    }
}
