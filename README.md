# reflection

Сервис для зеркалирования git-репозиториев: один source → N destinations (GitLab,
Gitea, Bitbucket, любой другой git-хостинг по HTTPS или SSH).

Каждый репозиторий сначала клонируется как bare-зеркало во временную директорию,
затем зеркало пушится во все указанные destination-репозитории. Синхронизация
запускается по расписанию и/или по вебхуку.

## Возможности

- Несколько репозиториев, у каждого — один источник и произвольное число назначений.
- Аутентификация источника/назначения по SSH-ключу или Personal Access Token (PAT).
- Индивидуальный или глобальный интервал синхронизации.
- Вебхук для мгновенного запуска синхронизации (одного репо или всех).
- Параллельная синхронизация репозиториев и параллельный push во все назначения.
- Dry-run режим для проверки конфигурации без реальных git-операций.
- Docker-образ, готовый к запуску через `docker compose`.

## Как это работает

```
source ──(git clone --mirror / remote update)──▶ локальный bare-клон ──(git push --mirror-refs)──▶ destination 1
                                                                    └────────────────────────────▶ destination 2
                                                                    └────────────────────────────▶ destination N
```

По умолчанию пушатся только ветки и теги:

```
+refs/heads/*:refs/heads/*
+refs/tags/*:refs/tags/*
```

`refs/pull/*` (GitHub PR-рефы) намеренно не пушатся — большинство хостингов
(Gitea, GitLab, Gitverse) отклоняют их хуком. При необходимости можно добавить
дополнительные refspec'и через `push_refs` для конкретного destination.

## Требования

- Python ≥ 3.11 и [uv](https://docs.astral.sh/uv/) — для локального запуска.
- Docker и Docker Compose — для контейнерного запуска (рекомендуется).
- Установленный `git` (в контейнере уже есть) и, при использовании SSH, `ssh`-клиент.

## Быстрый старт (Docker Compose)

```bash
git clone <url-этого-репозитория> reflection
cd reflection

cp config.example.yaml config.yaml
# отредактируйте config.yaml: repositories, интервалы, вебхук

mkdir -p keys                # если нужны SSH-ключи для приватных репозиториев
cp /path/to/deploy_key keys/github_ed25519
chmod 600 keys/github_ed25519

export WEBHOOK_SECRET=$(openssl rand -hex 32)
docker compose up -d --build
```

Проверка, что сервис поднялся:

```bash
curl -s http://localhost:8080/health
# {"status":"ok"}
```

## Локальный запуск без Docker

```bash
uv sync
uv run reflection --config config.yaml

# проверить конфиг без реальных git-операций
uv run reflection --config config.yaml --dry-run
```

## Конфигурация

Файл `config.yaml` (см. полный пример в [`config.example.yaml`](config.example.yaml))
состоит из двух секций: `settings` и `repositories`.

### `settings`

| Ключ                       | По умолчанию | Описание                                              |
|----------------------------|--------------|--------------------------------------------------------|
| `mirrors_dir`              | `/mirrors`   | Локальная директория для промежуточных bare-клонов     |
| `log_level`                | `INFO`       | `DEBUG` / `INFO` / `WARNING` / `ERROR`                  |
| `schedule_interval`        | `3600`       | Глобальный интервал синхронизации, сек (минимум 60)     |
| `timeout`                  | `300`        | Таймаут одной git-операции (fetch/push), сек            |
| `workers`                  | `4`          | Сколько репозиториев синхронизируется параллельно       |
| `webhook.enabled`          | `false`      | Включить встроенный HTTP-сервер вебхуков                |
| `webhook.host`             | `0.0.0.0`    | Адрес, на котором слушает вебхук-сервер                 |
| `webhook.port`             | `8080`       | Порт вебхук-сервера                                     |
| `webhook.secret`           | —            | Токен аутентификации; обязателен при `enabled: true`    |

### `repositories[]`

| Ключ                | Обязательно | Описание                                                        |
|---------------------|-------------|------------------------------------------------------------------|
| `name`              | да          | Уникальное имя репозитория (используется в путях и вебхуке)       |
| `schedule_interval` | нет         | Переопределяет `settings.schedule_interval` для этого репо        |
| `source`            | да          | Remote-объект источника                                          |
| `destinations[]`    | да (≥1)     | Список remote-объектов назначения                                 |

### Remote-объект (`source` / элемент `destinations[]`)

| Ключ         | Обязательно | Описание                                                                 |
|--------------|-------------|---------------------------------------------------------------------------|
| `url`        | да          | URL репозитория (HTTPS или `git@host:...`)                                |
| `ssh_key`    | нет         | Путь к приватному SSH-ключу внутри контейнера (см. `keys/`)               |
| `pat`        | нет         | Personal Access Token для HTTPS-аутентификации (см. ниже)                 |
| `ssl_verify` | нет (`true`)| `false` отключает проверку TLS-сертификата (`GIT_SSL_NO_VERIFY`)          |
| `push_refs`  | нет         | Доп. refspec'и поверх `+refs/heads/*` и `+refs/tags/*` (только `destinations`) |

### Переменные окружения в конфиге

Значения вида `${VAR_NAME}` в любом месте `config.yaml` подставляются из
переменных окружения процесса на этапе загрузки конфига. Это позволяет не
хранить секреты (токены, пароли) в самом файле:

```yaml
destinations:
  - name: gitlab
    url: https://oauth2:${GITLAB_TOKEN}@gitlab.com/mirror/my-project.git
```

### Аутентификация

- **SSH** — укажите `ssh_key` с путём внутри контейнера (обычно смонтированным
  из `./keys`). Ключ используется через `GIT_SSH_COMMAND` с
  `StrictHostKeyChecking=no` и `BatchMode=yes`.
- **PAT (HTTPS)** — укажите `pat`; токен подставляется как `oauth2:<pat>@host`
  через `url.<...>.insteadOf` в git-конфиге на время операции, не попадая в URL
  в логах и в `.git/config`.
- Можно указать логин/токен прямо в `url` (`https://user:TOKEN@host/...`), но
  предпочтительнее `pat` + `${VAR}`-подстановка, чтобы секрет не хранился в
  открытом виде в конфиге.

## Вебхук API

Доступен, только если `settings.webhook.enabled: true`. Аутентификация —
токен `settings.webhook.secret`, передаётся query-параметром `token`.

| Метод | Путь                  | Описание                                   |
|-------|-----------------------|---------------------------------------------|
| POST  | `/mirror/{repo_name}` | Запускает синхронизацию одного репозитория  |
| POST  | `/mirror`             | Запускает синхронизацию всех репозиториев   |
| GET   | `/health`             | Проверка живости, без аутентификации        |

Синхронизация выполняется в фоне, ответ `202 Accepted` возвращается сразу
после проверки токена (и существования репо для `/mirror/{repo_name}`).

```bash
curl -X POST "http://localhost:8080/mirror?token=$WEBHOOK_SECRET"
curl -X POST "http://localhost:8080/mirror/my-project?token=$WEBHOOK_SECRET"
```

При включённом вебхуке первая синхронизация всех репозиториев запускается
автоматически при старте сервиса (в фоновом потоке), затем работает по
расписанию как обычно.

## CLI

```
usage: reflection [-h] [-c FILE] [--dry-run]

-c, --config FILE   Путь к конфигу (по умолчанию: config.yaml)
--dry-run           Показать, что было бы сделано, без реальных git-операций
```

## Логи

Уровень логирования задаётся `settings.log_level`. Вывод — цветной,
в stdout (удобно для `docker logs`).

## Лицензия

[GPL-3.0](LICENSE)
