from io import BytesIO
import os
import logging
import ayayaxyz.helper as helper
from copy import copy
from telegram import Update, InputMediaPhoto, InlineKeyboardMarkup
from telegram.ext import (
    Application,
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
web_url = os.getenv("WEB_URL", "http://127.0.0.1:8080")


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(chat_id=update.effective_chat.id, text="Hi!")


def _pixiv_get_id(context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) == 0:
        return False, "You need to provide either an illustration ID or its url."
    if "https://www.pixiv.net/" in context.args[0] and "/artworks/" in context.args[0]:
        illust_id = int(context.args[0].split("/")[-1])
    else:
        try:
            illust_id = int(context.args[0])
        except ValueError:
            return False, "Invalid provided illustration ID."
    return True, illust_id


async def pixiv_id_cmd(
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        quick: bool = False,
        full_resolution: bool = False,
):
    message = update.effective_message
    get_id = _pixiv_get_id(context=context)
    if not get_id[0]:
        await helper.reply_error(message=message, text=get_id[1])
        return
    illust_id = get_id[1]
    error_buttons = None
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
            return await pixiv_id_cmd(update, context, quick=True)

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
            illust, pictures, quality=quality, limit=9, to_url=full_resolution
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
            media = []
            for x in illusts:
                if isinstance(x[0], BytesIO):
                    media.append(
                        InputMediaPhoto(
                            media=x[0].getvalue(), caption=caption, filename=x[1]
                        )
                    )
                elif isinstance(x[0], str):
                    print(x[0])
                    media_url = "{web}/pixiv/{url}".format(
                        web=web_url,
                        url=x[0],
                    )
                    print(media_url)
                    media.append(
                        InputMediaPhoto(
                            media=media_url,
                            caption=caption,
                            filename=x[1],
                        )
                    )
            msgs = await message.reply_media_group(
                media=media,
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
        sort_popular=False,
):
    message = update.effective_message
    get_id = _pixiv_get_id(context=context)
    if not get_id[0]:
        await helper.reply_error(message=message, text=get_id[1])
        return
    illust_id = get_id[1]
    if len(context.args) > 1:
        keyword = " ".join(context.args[1:])
        tags = [x.strip() for x in keyword.split(",")]
    notice_msg = await helper.reply_status(
        message=message,
        text="""Searching for image related to <code>{illust_id}</code>{with_tags}...""".format(
            illust_id=illust_id,
            with_tags=" with tags <code>{}</code>".format(", ".join(tags))
            if tags
            else "",
        ),
        silent=True,
    )
    try:
        illust = await pixiv.related_illust(illust_id, tags=tags, recurse=3)
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
    search_row = []
    if tags:

        async def cb_next(_: Update, __: CallbackContext):
            if sort_popular:
                tags.append("-P")
            clone_context = copy(context)
            clone_context.args = ",".join(tags).split(" ")
            return await pixiv_search_cmd(update, clone_context, quick=quick)

        search_row.append(("Next", cb_next, "pixiv-search-cb-next-{id}"))

    async def cb_related(_: Update, __: CallbackContext):
        clone_context = copy(context)
        clone_context.args = [str(illust["id"])]
        return await pixiv_related_cmd(
            update, clone_context, quick=quick, tags=tags, sort_popular=sort_popular
        )

    search_row.append(("Related", cb_related, "pixiv-search-cb-related-{id}"))

    async def cb_getoriginalres(cb_update: Update, _: CallbackContext):
        clone_context = copy(context)
        clone_context.args = [str(illust["id"])]
        return await pixiv_id_cmd(cb_update, clone_context)

    buttons = helper.buttons_build(
        [
            search_row,
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
    tags = [x.strip() for x in keyword.split(",")]
    related = True
    sort_popular = False
    sort = None
    # Telegram workaround when you type -- in chat
    if "-P" in tags or "--popular" in tags or "—popular" in tags:
        print("popular mode")
        try:
            tags.remove("-P")
        except ValueError:
            try:
                tags.remove("--popular")
            except ValueError:
                tags.remove("—popular")
        sort = "popular_desc"
        sort_popular = True
    if "--no-related" in tags or "—no-related" in tags:
        print("Disabling related image search...")
        try:
            tags.remove("--no-related")
        except ValueError:
            tags.remove("—no-related")
        related = False

    notice_msg = await helper.reply_status(
        message=message,
        text="""Searching for <code>{keyword}</code>{popular_mode}{no_related}...{notice}""".format(
            keyword=", ".join(tags),
            popular_mode=" in popular mode" if sort == "popular_desc" else "",
            no_related=" without searching related image" if not related else "",
            notice="\n<b>Note:</b> <code>qsearch</code> provides higher performance & stability in exchange for worse resolution"
            if not quick
            else "",
        ),
        silent=True,
    )

    try:
        illusts_search = await pixiv.search_illust(tags, sort=sort, related=related)
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
        return await pixiv_search_cmd(update, context, quick=quick)

    async def cb_related(_: Update, __: CallbackContext):
        clone_context = copy(context)
        clone_context.args = [str(illusts_search["id"])]
        return await pixiv_related_cmd(
            update, clone_context, quick=quick, tags=tags, sort_popular=sort_popular
        )

    async def cb_getoriginalres(cb_update: Update, _: CallbackContext):
        clone_context = copy(context)
        clone_context.args = [str(illusts_search["id"])]
        return await pixiv_id_cmd(cb_update, clone_context)

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
    message = update.effective_message
    try:
        command = context.args[0].lower()
        context.args = context.args[1:]
    except (ValueError, KeyError):
        await helper.reply_error(message=message, text="Please specify a sub-command.")
    else:
        match command:
            case "id":
                await pixiv_id_cmd(update, context)
            case "qid":
                await pixiv_id_cmd(update, context, quick=True)
            case "fid":
                await pixiv_id_cmd(update, context, full_resolution=True)
            case "search":
                await pixiv_search_cmd(update, context)
            case "qsearch":
                await pixiv_search_cmd(update, context, quick=True)
            case "related":
                await pixiv_related_cmd(update, context)
            case "qrelated":
                await pixiv_related_cmd(update, context, quick=True)
            case _:
                await helper.reply_error(message=message, text="Invalid command.")


def init_pixiv(application: Application) -> bool:
    try:
        if os.getenv("PIXIV_REFRESH_TOKEN"):
            logging.info("Logging into Pixiv using refresh token...")
            pixiv.login_token(os.getenv("PIXIV_REFRESH_TOKEN"))
        else:
            logging.info("Logging into Pixiv using credentials...")
            logging.warning("It's recommended to use refresh token to login instead.")
            pixiv.login(os.getenv("PIXIV_USERNAME"), os.getenv("PIXIV_PASSWORD"))
    except PixivLoginError as e:
        logging.error(
            "Logging into Pixiv failed, disabling Pixiv-related feature: {}".format(e)
        )
        return False
    pixiv.flask_api(app=app)
    logging.info("Loading Pixiv commands...")
    application.add_handler(CommandHandler("pixiv", pixiv_cmd))
    return True


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
    application = ApplicationBuilder().token(os.getenv("TOKEN")).build()
    init_pixiv(application=application)
    init_flask()
    logging.info("Loading default commands...")
    application.add_handlers([CommandHandler("start", start_cmd)])

    application.run_polling()
