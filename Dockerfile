FROM registry.cn-hangzhou.aliyuncs.com/gongbaowang/jre-env:8u241
COPY report-1.0.2.jar /app/
WORKDIR /app/
ENTRYPOINT ["/app/entrypoint.sh"]
