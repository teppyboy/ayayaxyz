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

### `pixiv`

#### `search`

Search an image from the given keywords which is seperated by ",".
You can optionally use `-P`/`--popular` to get only popular-related image, and `-<keyword>` to blacklist a keyword from search result.
> E.g: `/pixiv search Ayaka, Ayato, -Keqing`: This will search for image with "Ayaka", "Ayato" and *without* "Keqing" tag.

#### `id`

Fetch image(s) from the given ID/url, and optionally only fetch specified pages (seperated by " ")
> E.g: `/pixiv id https://www.pixiv.net/en/artworks/99945929` or `/pixiv id 99945929`

#### `related`

Search a related image from the given ID/url, and optionally specify tags (following `search` rules) to check the related image against, which will improve the image search result.

> E.g: `/pixiv related https://www.pixiv.net/en/artworks/99945929 Eula, -Keqing` or `/pixiv related 99945929`

#### `qsearch`

A quick variant of `search`, provides result faster but worse resolution.

#### `qid`

A quick variant of `id`, provides result faster but worse resolution.

#### `qrelated`

A quick variant of `related`, provides result faster but worse resolution.
