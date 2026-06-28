package com.kellen.bean;

import com.kellen.utils.exception.ApiExceptionHandler;
import lombok.extern.slf4j.Slf4j;
import org.springframework.web.bind.annotation.RestControllerAdvice;

/**
 * 异常增强
 *
 * @author sunkailun
 */
@Slf4j
@RestControllerAdvice
public class GlobalExceptionHandler extends ApiExceptionHandler {
}
