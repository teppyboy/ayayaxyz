import argparse
import os
import logging
import secrets
import ayayaxyz.helper as helper
from copy import copy
from telegram import Update, InputMediaPhoto, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    CallbackContext,
    CallbackQueryHandler,
)
from telegram.error import TelegramError
from telegram.helpers import escape_markdown
from ayayaxyz.api.pixiv import (
    Pixiv,
    PixivDownloadError,
    PixivSearchError,
    PixivLoginError,
)
from flask import Flask
from threading import Thread

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

pixiv = Pixiv()


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(chat_id=update.effective_chat.id, text="Hi!")


async def pixiv_fix_cmd(update: Update):
    message = update.effective_message
    notice_msg = await helper.reply_status(
        message, "Reloading current Pixiv instance...", True
    )
    try:
        init_pixiv()
    except PixivLoginError as e:
        await helper.edit_error(notice_msg, "Failed to restart Pixiv: {}".format(e))
        return
    await helper.edit_status(notice_msg, "Pixiv restarted successfully")


def _pixiv_get_id(context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) == 0:
        return (False, "You need to provide either an illustration ID or its url.")
    if "https://www.pixiv.net/" in context.args[0] and "/artworks/" in context.args[0]:
        illust_id = int(context.args[0].split("/")[-1])
    else:
        try:
            illust_id = int(context.args[0])
        except ValueError:
            return (False, "Invalid provided illustration ID.")
    return (True, illust_id)


async def pixiv_id_cmd(
    update: Update, context: ContextTypes.DEFAULT_TYPE, quick: bool = False
):
    message = update.effective_message
    get_id = _pixiv_get_id(context=context)
    if not get_id[0]:
        await helper.reply_error(message=message, text=get_id[1])
        return
    illust_id = get_id[1]

    notice_msg = await helper.reply_status(
        message=message,
        text="""Selected page(s): <code>{selected_images}</code>
Fetching <code>{illust_id}</code>...{notice}""".format(
            selected_images="all"
            if len(context.args) == 1
            else ", ".join([x for x in context.args[1:]]),
            illust_id=illust_id,
            notice="\n<b>Note</b>: <code>qid</code> does the same thing but provides higher performance & stability (in exchange for worse resolution)"
            if not quick
            else "",
        ),
        silent=True,
    )
    pictures = [int(x) - 1 for x in context.args[1:]]
    quality = "original"
    if quick:
        quality = "large"
    try:
        illusts = await pixiv.download_illust(
            await pixiv.get_illust_from_id(illust_id),
            pictures,
            quality=quality,
            limit=9,
        )
    except PixivDownloadError as e:
        await helper.edit_error(
            message=notice_msg,
            text="Failed to fetch illustration: <code>{}</code>".format(e),
        )
        return
    logging.info("Trying to send images bytes...")
    try:
        if len(illusts) == 1:
            await message.reply_photo(
                photo=illusts[0][0].getvalue(),
                filename=illusts[0][1],
                caption=escape_markdown(
                    "https://www.pixiv.net/en/artworks/{illust_id}".format(
                        illust_id=illust_id
                    ),
                    version=2,
                ),
                parse_mode="MarkdownV2",
            )
        else:
            await message.reply_media_group(
                media=[
                    InputMediaPhoto(media=x[0].getvalue(), filename=x[1])
                    for x in illusts
                ],
            )
        await notice_msg.delete()
    except TelegramError as e:
        await helper.edit_error(
            message=notice_msg, text="Failed to send images: <code>{}</code>".format(e)
        )
        logging.warning("Error while sending message: {}".format(e))


async def pixiv_related_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    quick: bool = False,
    tags: list[str] = None,
):
    message = update.effective_message
    get_id = _pixiv_get_id(context=context)
    if not get_id[0]:
        await helper.reply_error(message=message, text=get_id[1])
        return
    illust_id = get_id[1]

    notice_msg = await helper.reply_status(
        message=message,
        text="""Searching for image related to <code>{illust_id}</code>...""".format(
            illust_id=illust_id
        ),
        silent=True,
    )
    try:
        illust = await pixiv.related_illust(illust_id, tags=tags)
    except PixivSearchError as e:
        await helper.edit_error(
            message=notice_msg,
            text="Failed to search for related image: <code>{}</code>".format(e),
        )
        logging.warning("Error while searching for related image: {}".format(e))
        return

    quality = "original"
    if quick:
        quality = "large"
    try:
        illusts = await pixiv.download_illust(illust, [0], quality, 9)
    except PixivDownloadError as e:
        await helper.edit_error(
            message=notice_msg,
            text="Failed to fetch illustration: <code>{}</code>".format(e),
        )
        logging.warning("Error while downloading images: {}".format(e))
        return

    logging.info("Trying to send images bytes...")
    logging.info("Generating callback for button...")
    cb_nextimage_id = secrets.token_urlsafe(16)
    cb_relatedimage_id = secrets.token_urlsafe(16)

    async def cb_nextimage(_: Update, __: CallbackContext):
        clone_context = copy(context)
        clone_context.args = ",".join(tags).split(" ")
        await pixiv_search_cmd(update, clone_context, quick=quick)

    async def cb_relatedimage(_: Update, __: CallbackContext):
        clone_context = copy(context)
        clone_context.args = [str(illust["id"])]
        await pixiv_related_cmd(update, clone_context, quick=quick, tags=tags)

    context.application.add_handlers(
        [
            CallbackQueryHandler(
                cb_nextimage, "pixiv-search-cb-nextimage-{}".format(cb_nextimage_id)
            ),
            CallbackQueryHandler(
                cb_relatedimage,
                "pixiv-search-cb-relatedimage-{}".format(cb_relatedimage_id),
            ),
        ]
    )

    buttons = [
        [
            InlineKeyboardButton(
                "Next",
                callback_data="pixiv-search-cb-nextimage-{}".format(cb_nextimage_id),
            ),
            InlineKeyboardButton(
                "Related",
                callback_data="pixiv-search-cb-relatedimage-{}".format(
                    cb_relatedimage_id
                ),
            ),
        ]
    ]

    if quick:
        cb_getoriginalres_id = secrets.token_urlsafe(16)

        async def cb_getoriginalres(_: Update, __: CallbackContext):
            clone_context = copy(context)
            clone_context.args = [str(illust["id"])]
            await pixiv_id_cmd(update, clone_context)

        context.application.add_handler(
            CallbackQueryHandler(
                cb_getoriginalres,
                "pixiv-search-cb-getoriginalres-{}".format(cb_getoriginalres_id),
            )
        )
        buttons.append(
            [
                InlineKeyboardButton(
                    "Original Image",
                    callback_data="pixiv-search-cb-getoriginalres-{}".format(
                        cb_getoriginalres_id
                    ),
                )
            ]
        )

    try:
        await message.reply_photo(
            photo=illusts[0][0].getvalue(),
            filename=illusts[0][1],
            caption=escape_markdown(
                "https://www.pixiv.net/en/artworks/{illust_id}{notice}".format(
                    illust_id=illust["id"],
                    notice="\nThe image is in lower resolution, click 'Original image' to get full resolution"
                    if quality != "original"
                    else "",
                ),
                version=2,
            ),
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        )
        await notice_msg.delete()
    except TelegramError as e:
        await helper.edit_error(
            message=notice_msg, text="Failed to send images: <code>{}</code>".format(e)
        )
        logging.warning("Error while sending message: {}".format(e))


async def pixiv_search_cmd(
    update: Update, context: ContextTypes.DEFAULT_TYPE, quick: bool = False
):
    message = update.effective_message
    if len(context.args) == 0:
        await message.reply_text(text="No keyword provided.")
        return

    keyword = " ".join(context.args)
    parser = argparse.ArgumentParser()
    parser.add_argument("tags", type=str, nargs="*")
    parsed = parser.parse_args(keyword.split(","))
    tags = [x.strip() for x in parsed.tags]

    notice_msg = await helper.reply_status(
        message=message,
        text="""Searching for <code>{keyword}</code>...{notice}""".format(
            keyword=keyword,
            notice="\n<b>Note:</b> <code>qsearch</code> provides higher performance & stability in exchange for worse resolution"
            if not quick
            else "",
        ),
        silent=True,
    )

    try:
        illusts_search = await pixiv.search_illust(tags)
    except PixivSearchError as e:
        await helper.edit_error(
            message=notice_msg,
            text="Failed to search for image: <code>{}</code>".format(e),
        )
        logging.warning("Error while searching for images: {}".format(e))
        return

    quality = "original"
    if quick:
        quality = "large"
    try:
        illusts = await pixiv.download_illust(illusts_search, [0], quality, 9)
    except PixivDownloadError as e:
        await helper.edit_error(
            message=notice_msg,
            text="Failed to fetch illustration: <code>{}</code>".format(e),
        )
        logging.warning("Error while downloading images: {}".format(e))
        return

    logging.info("Trying to send images bytes...")
    logging.info("Generating callback for button...")
    cb_nextimage_id = secrets.token_urlsafe(16)
    cb_relatedimage_id = secrets.token_urlsafe(16)

    async def cb_nextimage(_: Update, __: CallbackContext):
        await pixiv_search_cmd(update, context, quick=quick)

    async def cb_relatedimage(_: Update, __: CallbackContext):
        clone_context = copy(context)
        clone_context.args = [str(illusts_search["id"])]
        await pixiv_related_cmd(update, clone_context, quick=quick, tags=tags)

    context.application.add_handlers(
        [
            CallbackQueryHandler(
                cb_nextimage, "pixiv-search-cb-nextimage-{}".format(cb_nextimage_id)
            ),
            CallbackQueryHandler(
                cb_relatedimage,
                "pixiv-search-cb-relatedimage-{}".format(cb_relatedimage_id),
            ),
        ]
    )

    buttons = [
        [
            InlineKeyboardButton(
                "Next",
                callback_data="pixiv-search-cb-nextimage-{}".format(cb_nextimage_id),
            ),
            InlineKeyboardButton(
                "Related",
                callback_data="pixiv-search-cb-relatedimage-{}".format(
                    cb_relatedimage_id
                ),
            ),
        ]
    ]

    if quick:
        cb_getoriginalres_id = secrets.token_urlsafe(16)

        async def cb_getoriginalres(_: Update, __: CallbackContext):
            clone_context = copy(context)
            clone_context.args = [str(illusts_search["id"])]
            await pixiv_id_cmd(update, clone_context)

        context.application.add_handler(
            CallbackQueryHandler(
                cb_getoriginalres,
                "pixiv-search-cb-getoriginalres-{}".format(cb_getoriginalres_id),
            )
        )
        buttons.append(
            [
                InlineKeyboardButton(
                    "Original Image",
                    callback_data="pixiv-search-cb-getoriginalres-{}".format(
                        cb_getoriginalres_id
                    ),
                )
            ]
        )

    try:
        await message.reply_photo(
            photo=illusts[0][0].getvalue(),
            filename=illusts[0][1],
            caption=escape_markdown(
                "https://www.pixiv.net/en/artworks/{illust_id}{notice}".format(
                    illust_id=illusts_search["id"],
                    notice="\nThe image is in lower resolution, click 'Original image' to get full resolution"
                    if quality != "original"
                    else "",
                ),
                version=2,
            ),
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        )
        await notice_msg.delete()
    except TelegramError as e:
        await helper.edit_error(
            message=notice_msg, text="Failed to send images: <code>{}</code>".format(e)
        )
        logging.warning("Error while sending message: {}".format(e))


async def pixiv_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    command = context.args[0].lower()
    context.args = context.args[1:]
    match command:
        case "id":
            await pixiv_id_cmd(update, context)
        case "qid":
            await pixiv_id_cmd(update, context, quick=True)
        case "search":
            await pixiv_search_cmd(update, context)
        case "qsearch":
            await pixiv_search_cmd(update, context, quick=True)
        case "fix":
            await pixiv_fix_cmd(update)
        case _:
            await update.effective_message.reply_text(text="Invalid command.")


def init_pixiv():
    try:
        if os.environ.get("PIXIV_REFRESH_TOKEN"):
            logging.info("Logging into Pixiv using refresh token...")
            pixiv.login_token(os.environ.get("PIXIV_REFRESH_TOKEN"))
        else:
            logging.info("Logging into Pixiv using credentials...")
            logging.warning("It's recommended to use refresh token to login instead.")
            pixiv.login(
                os.environ.get("PIXIV_USERNAME"), os.environ.get("PIXIV_PASSWORD")
            )
    except PixivLoginError as e:
        logging.error("Logging into Pixiv failed: {}".format(e))


def init_flask():
    app = Flask(__name__)

    @app.route("/")
    def root():
        return "AyayaXYZ is running correctly."

    thread = Thread(target=app.run, kwargs={"host": "0.0.0.0", "port": "8080"})
    thread.daemon = True
    thread.start()


def main():
    # Initialize task unrelated to Telegram bot itself.
    init_pixiv()
    init_flask()
    application = ApplicationBuilder().token(os.environ.get("TOKEN")).build()
    application.add_handlers(
        [CommandHandler("start", start_cmd), CommandHandler("pixiv", pixiv_cmd)]
    )

    application.run_polling()
