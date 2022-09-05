import argparse
import os
import logging
import ayayaxyz.helper as helper
from copy import copy
from telegram import Update, InputMediaPhoto, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    CallbackContext,
)
from telegram.error import TelegramError
from ayayaxyz.api.pixiv import (
    Pixiv,
    PixivDownloadError,
    PixivSearchError,
    PixivLoginError,
)
from flask import Flask
from waitress import serve
from threading import Thread

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)


app = Flask(__name__)
pixiv = Pixiv()
web_url = os.getenv("WEB_URL")


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
    if not quick:

        async def cb_tryqid(_: Update, __: CallbackContext):
            await pixiv_id_cmd(update, context, quick=True)

        error_buttons = helper.buttons_build(
            [[("Try again with qid", cb_tryqid, "pixiv-id-tryqid-{id}")]],
            application=context.application,
        )
    pictures = [int(x) - 1 for x in context.args[1:]]
    quality = "original"
    if quick:
        quality = "large"
    illust = await pixiv.get_illust_from_id(illust_id)
    try:
        illusts = await pixiv.download_illust(
            illust,
            pictures,
            quality=quality,
            limit=9,
        )
    except PixivDownloadError as e:
        msg_kwargs = {
            "message": notice_msg,
            "text": "Failed to fetch illustration: <code>{}</code>".format(e),
        }
        if not quick:
            msg_kwargs.update(
                {"reply_markup": InlineKeyboardMarkup(inline_keyboard=error_buttons)}
            )
        await helper.edit_error(**msg_kwargs)
        return
    logging.info("Trying to send images bytes...")
    caption = "https://www.pixiv.net/en/artworks/{illust_id}".format(
        illust_id=illust_id
    )
    try:
        if len(illusts) == 1:
            dl_button = helper.buttons_build(
                [
                    [
                        (
                            "Download",
                            None,
                            "{web}/pixiv/{url}".format(
                                web=web_url,
                                url=(
                                    await pixiv.get_illust_download_url(illust=illust)
                                )[0],
                            ),
                            "url",
                        )
                    ]
                ],
                application=context.application,
            )
            await message.reply_photo(
                photo=illusts[0][0].getvalue(),
                filename=illusts[0][1],
                caption=caption,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=dl_button),
            )
        else:
            msgs = await message.reply_media_group(
                media=[
                    InputMediaPhoto(media=x[0].getvalue(), filename=x[1])
                    for x in illusts
                ],
            )
            await helper.reply_html(msgs[-1], text=caption)
        await notice_msg.delete()
    except TelegramError as e:
        msg_kwargs = {
            "message": notice_msg,
            "text": "Failed to send images: <code>{}</code>".format(e),
        }
        if not quick:
            msg_kwargs.update(
                {"reply_markup": InlineKeyboardMarkup(inline_keyboard=error_buttons)}
            )
        await helper.edit_error(**msg_kwargs)
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

    async def cb_next(_: Update, __: CallbackContext):
        clone_context = copy(context)
        clone_context.args = ",".join(tags).split(" ")
        await pixiv_search_cmd(update, clone_context, quick=quick)

    async def cb_related(_: Update, __: CallbackContext):
        clone_context = copy(context)
        clone_context.args = [str(illust["id"])]
        await pixiv_related_cmd(update, clone_context, quick=quick, tags=tags)

    async def cb_getoriginalres(_: Update, __: CallbackContext):
        clone_context = copy(context)
        clone_context.args = [str(illust["id"])]
        await pixiv_id_cmd(update, clone_context)

    buttons = helper.buttons_build(
        [
            [
                ("Next", cb_next, "pixiv-search-cb-next-{id}"),
                ("Related", cb_related, "pixiv-search-cb-related-{id}"),
            ],
            [
                (
                    "Hi-res & All pages",
                    cb_getoriginalres,
                    "pixiv-search-cb-originalimage-{id}",
                ),
                (
                    "Download",
                    None,
                    "{web}/pixiv/{url}".format(
                        web=web_url,
                        url=(await pixiv.get_illust_download_url(illust=illust))[0],
                    ),
                    "url",
                ),
            ],
        ],
        application=context.application,
    )

    try:
        await message.reply_photo(
            photo=illusts[0][0].getvalue(),
            filename=illusts[0][1],
            caption="https://www.pixiv.net/en/artworks/{illust_id}{notice}".format(
                illust_id=illust["id"],
                notice="\nThis image has low resolution, click <i>Hi-res</i> to get higher resolution"
                if quality != "original"
                else "",
            ),
            parse_mode="HTML",
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

    logging.info("Generating callback for button...")

    async def cb_next(_: Update, __: CallbackContext):
        await pixiv_search_cmd(update, context, quick=quick)

    async def cb_related(_: Update, __: CallbackContext):
        clone_context = copy(context)
        clone_context.args = [str(illusts_search["id"])]
        await pixiv_related_cmd(update, clone_context, quick=quick, tags=tags)

    async def cb_getoriginalres(_: Update, __: CallbackContext):
        clone_context = copy(context)
        clone_context.args = [str(illusts_search["id"])]
        await pixiv_id_cmd(update, clone_context)

    buttons = helper.buttons_build(
        [
            [
                ("Next", cb_next, "pixiv-search-cb-next-{id}"),
                ("Related", cb_related, "pixiv-search-cb-related-{id}"),
            ],
            [
                (
                    "Hi-res & All pages",
                    cb_getoriginalres,
                    "pixiv-search-cb-originalimage-{id}",
                ),
                (
                    "Download",
                    None,
                    "{web}/pixiv/{url}".format(
                        web=web_url,
                        url=(
                            await pixiv.get_illust_download_url(illust=illusts_search)
                        )[0],
                    ),
                    "url",
                ),
            ],
        ],
        application=context.application,
    )

    try:
        await message.reply_photo(
            photo=illusts[0][0].getvalue(),
            filename=illusts[0][1],
            caption="https://www.pixiv.net/en/artworks/{illust_id}{notice}".format(
                illust_id=illusts_search["id"],
                notice="\nThis image has low resolution, click <i>Hi-res</i> to get higher resolution & all pages"
                if quality != "original"
                else "",
            ),
            parse_mode="HTML",
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
        if os.getenv("PIXIV_REFRESH_TOKEN"):
            logging.info("Logging into Pixiv using refresh token...")
            pixiv.login_token(os.getenv("PIXIV_REFRESH_TOKEN"))
        else:
            logging.info("Logging into Pixiv using credentials...")
            logging.warning("It's recommended to use refresh token to login instead.")
            pixiv.login(os.getenv("PIXIV_USERNAME"), os.getenv("PIXIV_PASSWORD"))
    except PixivLoginError as e:
        logging.error("Logging into Pixiv failed: {}".format(e))
    else:
        pixiv.flask_api(app=app)


def init_flask():
    @app.route("/")
    def root():
        return "AyayaXYZ is running correctly."

    thread = Thread(
        target=serve, kwargs={"app": app, "host": "0.0.0.0", "port": "8080"}
    )
    thread.daemon = True
    thread.start()


def main():
    # Initialize task unrelated to Telegram bot itself.
    init_pixiv()
    init_flask()
    application = ApplicationBuilder().token(os.getenv("TOKEN")).build()
    application.add_handlers(
        [CommandHandler("start", start_cmd), CommandHandler("pixiv", pixiv_cmd)]
    )

    application.run_polling()
