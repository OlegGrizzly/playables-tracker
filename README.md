# Playables Tracker

Трекер плеев YouTube Playables по дням: что залетает, что нет.

- **Сбор**: GitHub Actions раз в 2 часа скачивает каталог с [playables.games](https://playables.games/app/)
  (все ~2300 игр одним запросом) и сохраняет снимок на дату данных источника.
- **Дашборд**: `docs/` → GitHub Pages. Вкладки: 🔥 Залетает, 🆕 Новинки, 📋 Все игры,
  🧩 Жанры, 🏢 Издатели, ⭐ Сравнение. Поиск, фильтры, карточка игры с графиками.
- **Уведомления**: Telegram-бот присылает резкие пики, новинки, обгоняющие ровесников,
  и новые игры в топ-10 Hot.

## Структура

| Путь | Что |
|---|---|
| `tracker/collect.py` | скачивает страницу, достаёт `const DATA=[...]`, пишет снимок |
| `tracker/build.py` | считает метрики и собирает `docs/data.json` |
| `tracker/alerts.py` | ищет пики и шлёт в Telegram |
| `data/snapshots/YYYY-MM-DD.csv` | `id,plays` на дату — сырая история |
| `data/games.json` | метаданные: название, издатель, жанр, дата публикации, first/last seen |
| `data/alerts_sent.json` | что уже отправлено (чтобы не повторяться) |
| `docs/` | статический дашборд (`index.html`, `app.js`, `style.css`, `data.json`) |

Только стандартная библиотека Python 3.10+, без зависимостей.

## Метрики

- **В день** — средний прирост плеев в день за ≤ 7 дней по реальным снимкам. «<1K» — число не изменилось (темп меньше шага округления), «≈» — нет двух снимков, оценка `плеи / возраст`.
- **Рост/нед** — недельный прирост в % к плеям неделю назад.
- **Ускорение** — темп последних 3 дней / темп 7 дней до них (нужно ≥ 10 дней истории).
- **Ровесники** — перцентиль «плеев в день жизни» среди игр того же возраста (по всем снимкам).
- **🔥 Hot** = 45% перцентиль темпа + 35% ровесники + 20% перцентиль ускорения.
- **D1/D3/D7** — плеи на 1/3/7-й день после публикации.

Плеи округлены так, как их показывает YouTube (1.2K, 12K, 1.2M, 12M), поэтому у больших игр
дневной прирост «ступенчатый».

## Уведомления

| Сигнал | Условие |
|---|---|
| 📈 Резкий пик | прирост за сутки ≥ 3× медианы за прошлые 7 дней, ≥ 5K и ≥ 3 шагов округления |
| 🚀 Новинка залетает | возраст ≤ 7 дн, ровесники топ-3%, ≥ 1K плеев (один раз на игру) |
| 🔥 Новый в топ-10 Hot | впервые за 14 дней |

Пороги — константы в начале `tracker/alerts.py`.

Отдельно: если нового снимка нет больше 2 суток, бот пишет предупреждение со ссылкой на ручной запуск
(не чаще раза в сутки, пока снимки не появятся снова). Если расписание GitHub остановится полностью,
предупреждение не придёт: проверка запускается там же.

Запустить сбор вручную: Actions → «Сбор статистики Playables» → Run workflow (галочку теста не ставить).

## Внешний планировщик (основной запуск)

Расписание GitHub Actions ненадёжно: в первые дни GitHub выполнял 2 плановых запуска из ~10.
Поэтому сбор запускает внешний планировщик **cron-job.org** через GitHub API, а встроенное
расписание остаётся запасным.

Настройка задания на cron-job.org:

| Поле | Значение |
|---|---|
| URL | `https://api.github.com/repos/OlegGrizzly/playables-tracker/actions/workflows/collect.yml/dispatches` |
| Метод | `POST` |
| Расписание | каждые 2 часа (минута — не 0, например 23) |
| Заголовок `Authorization` | `Bearer <токен>` |
| Заголовок `Accept` | `application/vnd.github+json` |
| Заголовок `Content-Type` | `application/json` |
| Тело запроса | `{"ref":"main"}` |

Токен — fine-grained PAT (Settings → Developer settings → Fine-grained tokens): Repository access —
только `playables-tracker`, Permissions → Repository → **Actions: Read and write**, остальное не нужно.
Успешный ответ GitHub — `204 No Content`. При ошибке cron-job.org шлёт письмо.

Настройка:
1. В Telegram написать [@BotFather](https://t.me/BotFather) → `/newbot` → получить токен.
2. Написать боту любое сообщение, затем открыть `https://api.telegram.org/bot<ТОКЕН>/getUpdates`
   и взять `chat.id` (для группы/канала — добавить бота туда).
3. В репозитории: Settings → Secrets and variables → Actions:
   - Secrets: `TELEGRAM_BOT_TOKEN`; `TELEGRAM_CHAT_ID` — в Secrets или Variables
   - Variables: `DASHBOARD_URL` = адрес GitHub Pages (чтобы ссылки вели в карточку игры)

Проверка: Actions → «Сбор статистики Playables» → Run workflow → галочка «Только отправить тестовое сообщение».

Первый запуск алертов ничего не отправляет — только запоминает текущее состояние, чтобы не прислать сразу сотню сообщений.

## Локально

```bash
python3 tracker/collect.py          # снимок
python3 tracker/build.py            # docs/data.json
python3 tracker/alerts.py --dry-run # показать сигналы
python3 -m unittest discover -s tests
cd docs && python3 -m http.server 8000   # http://localhost:8000
```

## Ограничения

- История начинается с первого запуска — прошлые дни взять неоткуда.
- Мы зависим от вёрстки playables.games: если формат `const DATA` изменится, сбор упадёт
  (GitHub пришлёт письмо о падении workflow), а пустой снимок записан не будет.

## Запасной источник: YouTube напрямую

Если playables.games перестанет работать или сменит формат, данные можно брать
у самого YouTube. Проверено разовым запуском из GitHub Actions 2026-11-04
(код проверки удалён, здесь — только выводы).

**Что работает**

- Нужен регион, где доступен Playables (US, UK, CA, AU, IN, MY, TR, ЕС).
  Из России YouTube отвечает «Игротека недоступна в этой стране»; раннеры
  GitHub Actions находятся в США — с них работает.
- `GET https://www.youtube.com/playables?gl=US&hl=en` (cookie `SOCS=CAI`, чтобы
  не было окна согласия) — в HTML есть `ytInitialData` с первыми ~60 играми и
  `ytcfg` (`INNERTUBE_API_KEY`, `INNERTUBE_CLIENT_VERSION`, `VISITOR_DATA`).
- Остальной каталог — `POST /youtubei/v1/browse` с `browseId: FEmini_app_destination`
  и дальше по `continuationCommand.token`, ~60 игр за запрос
  (≈ 35 запросов на весь каталог из ~2100 игр).

**Что есть в карточке игры** (`miniGameCardViewModel`)

| Поле | Пример |
|---|---|
| `title` | `2 Player Battle` |
| `metadataText` | `6.2M plays` — округлено |
| `accessibilityText` | `2 Player Battle - 6.2 million plays - play Playable` — тоже округлено |
| `onTap...url` | `/playables/<id>` — тот же id, что у нас |
| `image.sources` | иконка |
| попап «Game info» (`overflowButton...`) | описание, жанр, **Developer** и **Publisher** отдельно, **Publish date** |

**Выводы**

- Точных чисел плеев YouTube не отдаёт — округление то же, что у playables.games.
- Данные YouTube свежее (обновляются постоянно, а не раз в сутки):
  2 Player Battle на YouTube 6.2M, в суточном снимке playables.games — 6.1M.
- Developer и Publisher у YouTube раздельные (у playables.games — одно имя).
- Минусы: это внутренний неофициальный API — формат может поменяться без
  предупреждения, запросы могут начать блокировать, сбор, вероятно, противоречит
  условиям использования YouTube. Поэтому основной источник — playables.games.
