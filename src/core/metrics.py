"""Метрики HTTP-сервиса для Prometheus."""

from prometheus_client import Counter, Histogram

HTTP_REQUESTS = Counter("http_requests_total", "HTTP requests", ["method", "path", "status"])
HTTP_DURATION = Histogram("http_request_duration_seconds", "HTTP duration", ["method", "path"])
