# Эндпоинты ingest

Все публичные пути начинаются с `/api/v1`. Ключ передаётся в
`X-Ingest-Key`; браузерная сессия и Bearer не используются.

| Метод и путь | Scope | Ввод | Успех |
|---|---|---|---|
| `POST /ingest/alerts` | `alert.ingest` | `AlertIngest` | `202 {receipt_id, status: "queued"}` |
| `POST /ingest/alertmanager` | `alert.ingest` | `AlertmanagerWebhook`, 1–50 алертов | `202 {receipt_ids, status: "queued"}` |
| `POST /ingest/deployments` | `deployment.ingest` | `DeploymentIngest` | `202 {receipt_id, status: "queued"}` |

`receipt_id` создаётся до публикации и передаётся в команде. Состояние
квитанции после обработки отдаёт сервис `incidents` (для алертов) или
`projects` (для выкаток). `202` не означает, что инцидент уже создан.

| Статус | Причина |
|---|---|
| `401` | ключ отсутствует, неизвестен, отозван или истёк |
| `403` | у ключа нет scope маршрута |
| `413` | тело больше 256 КиБ |
| `422` | неверная схема, проект или окружение не совпадают с ключом |
| `429` | token bucket проекта исчерпан; `Retry-After: 1` |
| `503` | Dragonfly, `projects`, `auth` или RabbitMQ недоступны; подтверждения публикации нет |

Ошибки используют `{"error": {"code", "message", "request_id"}}`.
Ключи и тела не логируются. `/health`, `/ready` и `/metrics` не проходят
через публичный маршрут Traefik.
