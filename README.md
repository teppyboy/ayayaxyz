# AyayaXYZ

A bot for Telegram that currently can interact with Pixiv

## Getting started

To run this bot, you need to have these environment variables (or put them in .env):

```bash
TOKEN=<Telegram bot token>
WEB_URL=<website url for API features>
PIXIV_REFRESH_TOKEN=<refresh-token>
# if PIXIV_REFRESH_TOKEN doesn't exist, it'll read username & password from env vars below
PIXIV_USERNAME=<username>
PIXIV_PASSWORD=<password>
```

Then use poetry to install project dependencies:

```bash
poetry install
# or poetry install -E login for logging in with credential support
```

And finally run the bot itself:

```bash
poetry run python -m ayayaxyz
```

## Commands

Currently there are 1 command available:

+ `pixiv`
  + `search`:
    Search an image from the given keywords (seperated by ",")
  + `id`:
    Fetch an image from the given ID/url, and optionally only fetch specified pages (seperated by " ")
  + `qsearch`:
    A quick variant of `search`, provides result faster but worse resolution.
  + `qid`:
    A quick variant of `id`, provides result faster but worse resolution.
