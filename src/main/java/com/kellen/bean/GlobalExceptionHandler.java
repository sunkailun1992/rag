package com.kellen.bean;

import com.kellen.rag.exception.ExternalServiceException;
import com.kellen.rag.exception.GraphSubmissionConflictException;
import com.kellen.rag.exception.GraphSubmissionInvalidException;
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

    @ExceptionHandler(GraphSubmissionInvalidException.class)
    public ResponseEntity<ApiResponse<Void>> graphSubmissionInvalid(GraphSubmissionInvalidException exception) {
        return ResponseEntity.status(HttpStatus.BAD_REQUEST)
                .body(ApiResponse.fail(ReturnCode.REQUEST_PARAMETER_ERROR, exception.getMessage()));
    }

    @ExceptionHandler(GraphSubmissionConflictException.class)
    public ResponseEntity<ApiResponse<Void>> graphSubmissionConflict(GraphSubmissionConflictException exception) {
        return ResponseEntity.status(HttpStatus.CONFLICT)
                .body(ApiResponse.fail(ReturnCode.REQUEST_PARAMETER_ERROR, exception.getMessage()));
    }

    @ExceptionHandler(AccessDeniedException.class)
    public ResponseEntity<ApiResponse<Void>> accessDeniedException(
            AccessDeniedException exception, HttpServletRequest request) {
        log.warn("Access denied, uri: [{}]", request.getRequestURI());
        return ResponseEntity.status(HttpStatus.FORBIDDEN)
                .body(ApiResponse.fail(ReturnCode.PERMISSION_DENIED));
    }

    @ExceptionHandler(ExternalServiceException.class)
    public ResponseEntity<ApiResponse<Void>> externalServiceException(
            ExternalServiceException exception, HttpServletRequest request) {
        log.error("External service request failed, service: [{}], status: [{}], uri: [{}]",
                exception.getServiceName(), exception.getStatusCode(), request.getRequestURI());
        return ResponseEntity.status(HttpStatus.BAD_GATEWAY)
                .body(ApiResponse.fail(ReturnCode.MIDDLEWARE_SERVICE_ERROR, exception.getPublicMessage()));
    }

}
