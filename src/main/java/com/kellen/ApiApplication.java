package com.kellen;

import org.apache.dubbo.config.spring.context.annotation.EnableDubbo;
import org.mybatis.spring.annotation.MapperScan;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.jdbc.autoconfigure.DataSourceAutoConfiguration;
import org.springframework.cache.annotation.EnableCaching;
import org.springframework.cloud.client.discovery.EnableDiscoveryClient;
import org.springframework.context.annotation.Bean;
import org.springframework.scheduling.annotation.EnableAsync;
import org.springframework.scheduling.annotation.EnableScheduling;
import org.springframework.transaction.annotation.EnableTransactionManagement;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.socket.server.standard.ServerEndpointExporter;

/**
 * RAG 知识检索服务启动类。
 *
 * <p>注册 RAG 知识检索模块基础组件，并开启缓存、异步、定时任务、Dubbo、
 * 事务和 MyBatis Mapper 扫描。</p>
 */
@EnableCaching
@SpringBootApplication(exclude = {DataSourceAutoConfiguration.class})
@RestController
@EnableDiscoveryClient
@EnableDubbo
@EnableAsync
@EnableScheduling
@EnableTransactionManagement
@MapperScan("com.kellen.*.mapper")
public class ApiApplication {

	/**
	 * JVM 进程入口，启动 RAG 知识检索服务。
	 *
	 * @param args 命令行参数
	 */
	public static void main(String[] args) {
		SpringApplication.run(ApiApplication.class, args);
	}

	/**
	 * 注册 WebSocket 端点导出器。
	 *
	 * @return WebSocket 端点导出器
	 */
	@Bean
	public ServerEndpointExporter serverEndpointExporter() {
		return new ServerEndpointExporter();
	}
}
