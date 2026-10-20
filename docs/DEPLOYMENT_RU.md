# Пошаговое развёртывание шлюза AI Mobile Agent на VPS

Это руководство намеренно разделяет подготовку GitHub, VPS, Ollama, Cloudflare и браузера. Успешный запуск контейнера ещё не означает, что приложение использует шлюз или что публичный доступ защищён.

## 0. Что у тебя должно получиться

- Мобильное веб-приложение останется статическим.
- URL шлюза будет вида https://llm.example.com/v1.
- На VPS шлюз слушает только 127.0.0.1:8787, опубликованный порт Ollama отсутствует.
- Ollama принимает соединения контейнера только на IP Docker bridge, а не на публичном IP сервера.
- Запрос без правильного Bearer-ключа к /v1 и /admin возвращает 401.
- Два одинаковых завершённых запроса с temperature=0 дают X-AI-Cache: MISS, затем HIT.
- Семантический кэш и дополнительные фоновые сервисы пока не устанавливаются.

## 1. Проверка GitHub и локальной конфигурации

1. Открой репозиторий https://github.com/DorpNay/myAI.
2. Убедись, что PR #2 в истории объединён с main.
3. В Actions открой workflow **Cache gateway tests**. Успешный прогон для merge-коммита PR #2 уже зафиксирован; для последующих изменений нужно дождаться нового прогона.
4. На странице GitHub Pages открой работающий сайт приложения, если он уже опубликован. Скопируй адрес из строки браузера; не угадывай домен.
5. Запиши **origin** приложения: схема + домен + необязательный порт, но без пути. Например, для https://account.github.io/myAI/ origin — https://account.github.io. Он понадобится для ALLOWED_ORIGINS.

Не размещай API-ключ в index.html, GitHub Pages, тикетах, чатах или скриншотах.

## 2. Регистрация и проверка Vultr (только если VPS ещё не создан)

Если действующий сервер уже есть, пропусти создание нового и переходи к разделу 3.

1. Открой https://www.vultr.com/ и создай аккаунт по официальной форме регистрации.
2. Подтверди email и заверши проверки аккаунта, которые запросит Vultr. Способ оплаты и возможная предварительная авторизация зависят от аккаунта и региона; проверь условия на экране перед подтверждением.
3. Проверь страницу Billing/Credits. Не считай рекламный купон, приветственный кредит или бесплатный период гарантированным, пока он явно не показан в твоём аккаунте. До создания сервера посмотри тариф и сумму списаний после исчерпания кредита.
4. Если сервер нужен, создай **Cloud Compute → Shared CPU**, Ubuntu 24.04 LTS x64, 4 vCPU и 8 GB RAM (или используй уже оплаченный сервер с такими ресурсами). Регион выбери по близости к себе и фактической задержке.
5. Сохрани IP сервера и доступ к нему в менеджере паролей. Не отправляй пароль в переписку.
6. Для работы без локального ПК используй веб-консоль Vultr: выбери instance → Console/View Console. Войди как root или в пользователя, созданного при установке.

**Контроль бюджета:** проверь прогноз ежемесячной стоимости, цену хранения/снимков и правила начисления после выключения. Выключенный экземпляр не обязательно означает нулевой счёт за все ресурсы.

## 3. Первичная диагностика VPS

В веб-консоли выполни:

    apt update
    apt install -y curl git ca-certificates openssl nano ufw
    free -h
    df -h
    docker --version
    docker compose version
    systemctl is-active ollama
    ollama list

Если Docker/Compose или Ollama отсутствуют, установи их по официальной инструкции соответствующего проекта; не запускай второй экземпляр сервиса поверх существующего. Для Docker используй официальный Docker Engine/Compose install guide, для Ollama — https://ollama.com/download/linux.

До изменения конфигурации сохрани текущие настройки:

    mkdir -p /root/ai-gateway-backup
    systemctl cat ollama > /root/ai-gateway-backup/ollama-unit.txt
    cp -a /etc/systemd/system/ollama.service.d /root/ai-gateway-backup/ 2>/dev/null || true
    ufw status numbered

Если на сервере уже работает Dify, не пересоздавай его и не меняй публичный hostname, через который ты к нему обращаешься.

## 4. Настройка безопасной связи Docker → Ollama

Контейнер шлюза использует host.docker.internal, добавленный в Compose. На Linux это имя разрешается в host gateway. Чтобы API модели не открылся на публичном интерфейсе, Ollama должен слушать IP Docker bridge, а не 0.0.0.0.

1. Узнай gateway основной Docker bridge-сети:

    docker network inspect bridge --format '{{(index .IPAM.Config 0).Gateway}}'

2. Команда должна вывести IP вроде 172.17.0.1. Используй ровно тот IP, который получил, — не копируй пример вслепую.
3. Убедись, что Ollama установлена и модели доступны командой ollama list. Если список пуст, скачай одну подходящую небольшую модель и проверь её точный тег, например:

    ollama pull qwen3:1.7b
    ollama list

Не скачивай несколько моделей просто «на всякий случай»: на 8 GB RAM важнее свободная память, чем количество весов на диске.

4. Создай или отредактируй override Ollama:

    mkdir -p /etc/systemd/system/ollama.service.d
    nano /etc/systemd/system/ollama.service.d/override.conf

Вставь блок ниже, заменив 172.17.0.1 на IP, полученный в пункте 1:

    [Service]
    Environment="OLLAMA_HOST=172.17.0.1:11434"
    Environment="OLLAMA_NUM_PARALLEL=1"
    Environment="OLLAMA_MAX_LOADED_MODELS=1"
    Environment="OLLAMA_KEEP_ALIVE=5m"

5. Сохрани файл и перезапусти сервис:

    systemctl daemon-reload
    systemctl restart ollama
    systemctl --no-pager --full status ollama
    ss -lntp | grep 11434

В выводе ss ожидай IP Docker bridge и порт 11434. Если видишь 0.0.0.0:11434 или публичный адрес, остановись и исправь привязку до продолжения. **Не открывай 11434 в UFW или firewall Vultr.**

Если уже работающий Dify подключался к Ollama через другой адрес, после смены привязки проверь его подключение отдельно. Сохраняй остальные параметры действующего сервиса.

## 5. Обновление репозитория на VPS

Если репозитория на сервере ещё нет:

    cd /opt
    git clone https://github.com/DorpNay/myAI.git
    cd /opt/myAI

Если репозиторий уже клонирован, перейди в его каталог и проверь состояние:

    git status --short
    git switch main
    git pull --ff-only origin main

Не выполняй git reset --hard, если в каталоге есть важные изменения. В production запускаем шлюз из gateway/ внутри этого репозитория.

## 6. Создание ключа и настройка шлюза

1. Перейди в каталог:

    cd /opt/myAI/gateway
    cp .env.example .env
    chmod 600 .env

Если .env уже существует, не перезаписывай его: сначала сохрани копию и отредактируй текущий файл.

2. Сгенерируй секрет:

    openssl rand -hex 32

3. Открой конфигурацию:

    nano .env

4. Укажи:
   - GATEWAY_API_KEY — случайный секрет из предыдущего шага.
   - ALLOWED_ORIGINS — точный origin из раздела 1, без пути, например https://account.github.io.
   - OLLAMA_BASE_URL оставь http://host.docker.internal:11434, если Docker bridge и привязка Ollama настроены по разделу 4.
   - TTL и лимиты оставь по умолчанию для первого испытания.

Проверь, что ALLOWED_ORIGINS не равен *, а GATEWAY_API_KEY не равен replace-with-a-random-secret. Не копируй реальный секрет в документацию или GitHub Issues. Файл .env не коммить.

5. Запусти шлюз:

    docker compose up -d --build
    docker compose ps
    docker compose logs --tail=100 cache-gateway
    curl --fail http://127.0.0.1:8787/health

Ожидаемый ответ health: {"status":"ok"}. Если контейнер перезапускается или healthcheck падает, смотри логи; не открывай дополнительные порты для «быстрого исправления».

## 7. Регистрация и настройка Cloudflare

Если у тебя ещё нет аккаунта Cloudflare:

1. Открой https://dash.cloudflare.com/ и зарегистрируй аккаунт.
2. Подтверди email и заверши требования безопасности аккаунта. Включи MFA, если доступно.
3. Для постоянного hostname нужен собственный домен, подключённый к Cloudflare. Cloudflare Tunnel не выдаёт бесплатный зарегистрированный домен. Если домена нет, зарегистрируй его у выбранного регистратора, затем добавь домен в Cloudflare. Если Cloudflare выдаст новые nameservers, поменяй nameservers у регистратора и дождись активации зоны.
4. Открой Cloudflare Zero Trust → Networks → Tunnels. Сначала посмотри, есть ли уже работающий Tunnel.
5. Если Tunnel уже используется для Dify, не удаляй его и не меняй существующий маршрут. Добавь отдельный hostname к тому же активному Tunnel либо создай отдельный Tunnel с названием вроде ai-mobile-agent.
6. Для нового Tunnel нажми Create Tunnel и выбери cloudflared. Открой вариант установки для Linux/Debian, выполняй текущие команды, которые показывает сама панель Cloudflare, и скопируй Tunnel token только в терминал VPS. Не вставляй токен в GitHub или чат.
7. Рекомендуемый вариант для этой архитектуры — запустить cloudflared как нативную службу на VPS, чтобы сервис маршрутизировал запрос на http://localhost:8787. Если твой текущий connector работает как Docker-контейнер, не используй для него localhost:8787: localhost внутри контейнера — это сам контейнер, а порт шлюза намеренно опубликован только на loopback хоста. Сначала спланируй безопасную связку, не открывая порт наружу.
8. В настройках Tunnel добавь Public Hostname:
   - Subdomain: llm (или другое свободное имя).
   - Domain: домен, который уже активен в Cloudflare.
   - Service type: HTTP.
   - URL: localhost:8787, только если cloudflared работает на хосте.
9. Сохрани маршрут. В результате должен появиться HTTPS hostname наподобие https://llm.example.com. Не переназначай существующий адрес Dify на порт шлюза.

Quick Tunnel с адресом trycloudflare.com годится только для временной проверки. Его адрес может измениться, поэтому не используй такой URL как постоянную настройку приложения.

## 8. Подключение мобильного приложения

1. Открой работающий сайт AI Mobile Agent в личном браузере.
2. Перейди в ⚙️ Настройки.
3. В поле Base URL укажи https://llm.example.com/v1 — замени hostname своим. Суффикс /v1 обязателен; интерфейс сам добавляет /chat/completions.
4. В поле API Key введи GATEWAY_API_KEY из файла .env на VPS.
5. В Model введи точный тег из ollama list, например qwen3:1.7b — только если этот тег действительно установлен.
6. Нажми Сохранить.
7. Убедись, что запрос работает и обычный ответ приходит на русском.

Ключ хранится в localStorage этого браузера. Не пользуйся чужим устройством и не вставляй секрет в код HTML. Если страница доступна другим людям, персональный ключ не делает приложение автоматически подходящим для общего использования; для этого нужна отдельная пользовательская авторизация/периметр доступа.

## 9. Приёмочные испытания

На VPS запусти:

    cd /opt/myAI/gateway
    KEY="$(grep '^GATEWAY_API_KEY=' .env | cut -d= -f2-)"

### Проверка авторизации

    curl -i http://127.0.0.1:8787/v1/models
    curl -i -H "Authorization: Bearer $KEY" http://127.0.0.1:8787/v1/models

Первая команда должна вернуть 401. Вторая — успешный список моделей. Если второй вызов падает, смотри логи gateway и Ollama, не открывая 11434 извне.

### Проверка кэша

Заменяй имя модели на фактически установленное:

    BODY='{"model":"qwen3:1.7b","messages":[{"role":"user","content":"Reply exactly CACHE-CHECK-42"}],"temperature":0}'
    for i in 1 2; do
      curl -sS -D - -o /tmp/cache-test-$i.json         -H "Authorization: Bearer $KEY"         -H "Content-Type: application/json"         --data "$BODY" http://127.0.0.1:8787/v1/chat/completions         | grep -i '^x-ai-cache:'
    done

Ожидаемые заголовки: сначала MISS, затем HIT. Если второй запрос снова MISS, сначала проверь finish_reason в JSON, модель и одинаковость тела запроса.

### Проверка статистики и ресурсов

    curl -sS -H "Authorization: Bearer $KEY" http://127.0.0.1:8787/admin/cache/stats
    free -h
    docker stats --no-stream
    docker compose logs --tail=100 cache-gateway
    ss -lntp | grep 11434

Зафиксируй результаты и фактическое потребление RAM при простое и при генерации. Не считай ожидаемые показатели измеренными, пока их не увидел на VPS.

### Проверка динамического обхода

Повтори тест с заголовком X-AI-Cache-Bypass: 1. Заголовок ответа должен быть BYPASS; второй запрос не должен получить кешированный ответ. Аналогично потоковые и инструментальные запросы не должны попадать в SQLite кэш.

## 10. Как обновлять и откатывать

Перед обновлением проверь новый CI в GitHub Actions. Затем на VPS:

    cd /opt/myAI
    git status --short
    git pull --ff-only origin main
    cd gateway
    docker compose up -d --build
    docker compose ps
    curl --fail http://127.0.0.1:8787/health

Если контейнер не стартует, смотри логи и вернись к предыдущему проверенному commit. Не удаляй named volume с кэшем для обычного рестарта: docker compose down сохраняет volume, а docker compose down -v его удаляет. Для отката Ollama верни резервную копию override-файла и выполни systemctl daemon-reload && systemctl restart ollama.

## 11. Что означает «готово»

Система принимается только после всех пунктов:
- Новый GitHub Actions workflow успешен.
- Gateway healthcheck стабилен после перезапуска VPS/контейнера.
- Без ключа API отвечает 401.
- Один и тот же запрос даёт MISS → HIT.
- Stream, tools, incomplete generation, multimodal and explicit bypass не кэшируются.
- Ollama слушает адрес Docker bridge, порт не открыт на public IP и не разрешён firewall.
- Cloudflare HTTPS route ведёт на gateway, при этом рабочий маршрут Dify не изменён.
- В браузере Base URL, ключ и тег модели проверены реальным запросом.
- RAM проверена во время нагрузки; нет OOM events.

До завершения сквозных испытаний шлюз считай подготовленным к развёртыванию, а не доказанно работающим в production.
