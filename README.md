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
# If you want to change log level (default is INFO)
LOGLEVEL=DEBUG
```

Then use poetry to install project dependencies:

```bash
poetry install
# or poetry install -E login for logging in with credential support (Chrome required)
```

And finally run the bot itself:

```bash
poetry run python -m ayayaxyz
```

## Commands

Currently there is 1 command available:

### `pixiv`

#### `search`

Search an image from the given keywords, which is seperated by ",".

+ `-P`/`--popular`: Search only popular-related images.
+ `--no-related`: Do not search for related image in the search algorithm *(more duplicated images!)*
+ `--no-tl`/`--no-translate-tags`: Do not translate tags from English to Japanese (e.g: Nino Nakano won't be translated to 中野二乃 before searching)
+ `-<tag>` to exclude `<tag>` from search result (can be specified multiple times)

> E.g: `/pixiv search Ayaka, Ayato, -P, --no-related, -Keqing`: This will search for image with "Ayaka", "Ayato" *without* searching for related image and "Keqing" tag.

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

#### `fid`

An experimental variant of `id` which uses AyayaXYZ internal server to post Telegram images instead of uploading it directly.

By doing this it'll achieve faster upload speed (even faster than `qid`) and provides nearly-good image (same as `id`) (original image is not possible since Telegram compresses images by bot)
but a major drawback is if the webserver dies, this function will stop working.

#### `qrelated`

A quick variant of `related`, provides result faster but worse resolution.
