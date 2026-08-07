package com.kellen.bean;

import com.kellen.rag.exception.ExternalServiceException;
import com.kellen.utils.enumeration.ReturnCode;
import com.kellen.utils.exception.ApiExceptionHandler;
import com.kellen.utils.response.ApiResponse;
import jakarta.servlet.http.HttpServletRequest;
import lombok.extern.slf4j.Slf4j;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;

/**
 * 异常增强
 *
 * @author sunkailun
 */
@Slf4j
@RestControllerAdvice
public class GlobalExceptionHandler extends ApiExceptionHandler {

    @ExceptionHandler(AccessDeniedException.class)
    public ResponseEntity<ApiResponse<Void>> accessDeniedException(
            AccessDeniedException exception, HttpServletRequest request) {
        log.warn("Access denied, uri: [{}]", request.getRequestURI());
        return ResponseEntity.status(HttpStatus.FORBIDDEN)
                .body(ApiResponse.fail(ReturnCode.无权限使用));
    }

    @ExceptionHandler(ExternalServiceException.class)
    public ResponseEntity<ApiResponse<Void>> externalServiceException(
            ExternalServiceException exception, HttpServletRequest request) {
        log.error("External service request failed, service: [{}], status: [{}], uri: [{}]",
                exception.getServiceName(), exception.getStatusCode(), request.getRequestURI());
        return ResponseEntity.status(HttpStatus.BAD_GATEWAY)
                .body(ApiResponse.fail(ReturnCode.中间件服务出错, exception.getPublicMessage()));
    }

    @ExceptionHandler(Exception.class)
    public ResponseEntity<ApiResponse<Void>> unhandledException(
            Exception exception, HttpServletRequest request) {
        log.error("Unhandled request failure, uri: [{}], exception: [{}]",
                request.getRequestURI(), exception.getClass().getName(), exception);
        return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR)
                .body(ApiResponse.fail(ReturnCode.系统执行出错, "系统处理失败，请查看服务日志"));
    }
}
