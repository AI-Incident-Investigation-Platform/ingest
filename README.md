# ingest

Приём алертов и выкаток AIIP. Сервис проверяет `X-Ingest-Key` до чтения
тела, ограничивает запрос по проекту и публикует команду в RabbitMQ с
publisher confirm. `202` означает, что брокер подтвердил публикацию;
создание инцидента и дедупликацию выполняет `incidents`.

У сервиса нет БД. Он читает API-токены проекта из Dragonfly и, при промахе,
из `projects` по служебному API. Токен должен быть привязан к окружению.

## Документация

- [Эндпоинты](docs/endpoints.md)
- [Устройство и решения](docs/design.md)
- [Требования и статус](docs/requirements.md)

## Разработка

Python 3.14, uv, Docker и инфра AIIP. Сначала получи параметры инфраструктуры
через `make -C ../infra service name=ingest`, затем `make config` и заполни
локальный `config.toml`: адреса и учётные данные Dragonfly/RabbitMQ, а также
`auth.client_secret`, выданный `auth` для клиента `ingest`. Секреты в git
не добавляются.

```bash
make help
make check
make test
make test-all
make run     # на хосте, нужен make -C ../infra forward
make up      # образ, Secret и Deployment в k3d aiip
make ps
make logs
```

`make test` и `make test-all` сейчас выполняют unit-сценарии. Живой
интеграционный прогон с `projects`, `auth`, Dragonfly и RabbitMQ нужен
после готовности соседних сервисов и их учётных данных.
