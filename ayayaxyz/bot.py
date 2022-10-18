from io import BytesIO
import os
import logging

import telegram

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

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("ayayaxyz")

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


async def _pixiv_dl_illust(
    quick: bool, illust, pictures: list[int], message: telegram.Message
) -> list | dict:
    quality = "original"
    if quick:
        quality = "large"
    try:
        illusts = await pixiv.download_illust(
            illust=illust, pictures=pictures, quality=quality, limit=9
        )
    except PixivDownloadError as e:
        msg_kwargs = {
            "message": message,
            "text": "Failed to fetch illustration: <code>{}</code>".format(e),
        }
        logger.warning("Error while downloading images: {}".format(e))
        return msg_kwargs
    return illusts


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
    try:
        pictures = [int(x) - 1 for x in context.args[1:]]
    except ValueError:
        await helper.edit_error(message=notice_msg, text="Pages list must be integers")
        return
    illust = await pixiv.get_illust_from_id(illust_id)
    illusts = await _pixiv_dl_illust(
        quick=quick, illust=illust, pictures=pictures, message=notice_msg
    )
    if illusts is dict:
        if not quick:
            illusts.update(
                {"reply_markup": InlineKeyboardMarkup(inline_keyboard=error_buttons)}
            )
        await helper.edit_error(**illusts)
        return
    logger.debug("Trying to send images bytes...")
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
                    logger.debug(x[0])
                    media_url = "{web}/pixiv/{url}".format(
                        web=web_url,
                        url=x[0],
                    )
                    logger.debug(media_url)
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
        logger.warning("Error while sending message: {}".format(e))


async def pixiv_related_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    quick: bool = False,
    tags: list[str] = None,
    sort_popular: bool = False,
    no_related: bool = False,
    translate_tags: bool = True,
):
    message = update.effective_message
    get_id = _pixiv_get_id(context=context)
    if not get_id[0]:
        await helper.reply_error(message=message, text=get_id[1])
        return
    illust_id = get_id[1]
    tags_orig = None
    if len(context.args) > 1:
        keyword = " ".join(context.args[1:])
        tags = [x.strip() for x in keyword.split(",")]
    if tags:
        tags_orig = copy(tags)
        if not translate_tags:
            _tl_args = {
                "--no-tl",
                "—no-tl",
                "--no-translate-tags",
                "—no-translate-tags",
            }.intersection(set(tags))
            if _tl_args:
                logger.debug("Tag translation disabled.")
                for arg in _tl_args:
                    tags.remove(arg)
                translate_tags = False
            else:
                translate_tags = True
        if translate_tags:
            try:
                tags = await pixiv.translate_tags(tags=tags)
            except PixivSearchError:
                pass

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
        logger.warning("Error while searching for related image: {}".format(e))
        return

    illusts = await _pixiv_dl_illust(
        quick=quick, illust=illust, pictures=[0], message=notice_msg
    )
    if illusts is dict:
        await helper.edit_error(**illusts)
        return

    logger.debug("Trying to send images bytes...")
    search_row = []
    if tags:

        async def cb_next(_: Update, __: CallbackContext):
            if sort_popular:
                tags_orig.append("-P")
            if no_related:
                tags_orig.append("--no-related")
            print(type(tags_orig))
            clone_context = copy(context)
            clone_context.args = ",".join(tags_orig).split(" ")
            return await pixiv_search_cmd(
                update, clone_context, quick=quick, translate_tags=translate_tags
            )

        search_row.append(("Next", cb_next, "pixiv-search-cb-next-{id}"))

    async def cb_related(_: Update, __: CallbackContext):
        clone_context = copy(context)
        clone_context.args = [str(illust["id"])]
        return await pixiv_related_cmd(
            update,
            clone_context,
            quick=quick,
            tags=tags_orig,
            sort_popular=sort_popular,
            no_related=no_related,
            translate_tags=translate_tags,
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
                if quick
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
        logger.warning("Error while sending message: {}".format(e))


async def pixiv_search_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    quick: bool = False,
    translate_tags: bool = None,
):
    message = update.effective_message
    keyword = " ".join(context.args)
    tags = [x.strip() for x in keyword.split(",")]
    tags_orig = copy(tags)
    related = True
    sort_popular = False
    sort = None
    quality = "original"
    notice_msg = None
    # Telegram workaround when you type -- in chat
    _help_tags = {"-H", "--help", "—help"}.intersection(set(tags))
    _p_tags = {"-P", "--popular", "—popular"}.intersection(set(tags))
    _no_related_tags = {"--no-related", "—no-related"}.intersection(set(tags))
    logger.debug("{} {}".format(_p_tags, _no_related_tags))
    if _help_tags:
        await helper.reply_html(
            message=message,
            text="<b>Help:</b> https://github.com/teppyboy/ayayaxyz#{}".format(
                "qsearch" if quick else "search"
            ),
        )
        return

    if _p_tags:
        logger.debug("popular mode")
        for arg in _p_tags:
            tags.remove(arg)
        sort = "popular_desc"
        sort_popular = True

    if _no_related_tags:
        logger.debug("Related image search disabled.")
        for arg in _no_related_tags:
            tags.remove(arg)
        related = False

    if not translate_tags:
        _tl_args = {
            "--no-tl",
            "—no-tl",
            "--no-translate-tags",
            "—no-translate-tags",
        }.intersection(set(tags))
        if _tl_args:
            logger.debug("Tag translation disabled.")
            for arg in _tl_args:
                tags.remove(arg)
            translate_tags = False
        else:
            translate_tags = True

    if len(tags) == 0:
        await helper.reply_error(message=message, text="No keyword provided.")
        return

    if translate_tags:
        notice_msg = await helper.reply_status(
            message=message,
            text="""Translating tags <code>{keyword}</code>...""".format(
                keyword=", ".join(tags),
            ),
            silent=True,
        )
        try:
            tags = await pixiv.translate_tags(tags=tags)
        except PixivSearchError:
            pass

    search_txt = "Searching for <code>{keyword}</code>{popular_mode}{no_related}...{notice}".format(
        keyword=", ".join(tags),
        popular_mode=" in popular mode" if sort == "popular_desc" else "",
        no_related=" without searching related image" if not related else "",
        notice="\n<b>Note:</b> "
        + "<code>qsearch</code> provides higher performance & stability in exchange for worse resolution"
        if not quick
        else "",
    )
    if not notice_msg:
        notice_msg = await helper.reply_status(
            message=message,
            text=search_txt,
        )
    else:
        await helper.edit_status(
            message=notice_msg,
            text=search_txt,
        )

    try:
        illusts_search = await pixiv.search_illust(tags, sort=sort, related=related)
    except PixivSearchError as e:
        await helper.edit_error(
            message=notice_msg,
            text="Failed to search for image: <code>{}</code>".format(e),
        )
        logger.warning("Error while searching for images: {}".format(e))
        return

    illusts = await _pixiv_dl_illust(
        quick=quick, illust=illusts_search, pictures=[0], message=notice_msg
    )
    if illusts is dict:
        await helper.edit_error(**illusts)
        return

    logger.debug("Generating callback for button...")

    async def cb_next(_: Update, __: CallbackContext):
        return await pixiv_search_cmd(
            update, context, quick=quick, translate_tags=translate_tags
        )

    async def cb_related(_: Update, __: CallbackContext):
        clone_context = copy(context)
        clone_context.args = [str(illusts_search["id"])]
        return await pixiv_related_cmd(
            update,
            clone_context,
            quick=quick,
            tags=tags_orig,
            sort_popular=sort_popular,
            no_related=not related,
            translate_tags=translate_tags,
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
                if quick
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
        logger.warning("Error while sending message: {}".format(e))


async def pixiv_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    try:
        command = context.args[0].lower()
        context.args = context.args[1:]
    except (ValueError, KeyError, IndexError):
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
            logger.info("Logging into Pixiv using refresh token...")
            pixiv.login_token(os.getenv("PIXIV_REFRESH_TOKEN"))
        else:
            logger.info("Logging into Pixiv using credentials...")
            logger.warning("It's recommended to use refresh token to login instead.")
            pixiv.login(os.getenv("PIXIV_USERNAME"), os.getenv("PIXIV_PASSWORD"))
    except PixivLoginError as e:
        logger.error(
            "Logging into Pixiv failed, disabling Pixiv-related feature: {}".format(e)
        )
        return False
    pixiv.flask_api(app=app)
    logger.info("Loading Pixiv commands...")
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
    logging.info("Initializing logging...")
    loglevel = os.getenv("LOGLEVEL", "INFO")
    logger.setLevel(loglevel)
    application = ApplicationBuilder().token(os.getenv("TOKEN")).build()
    init_pixiv(application=application)
    init_flask()
    logger.info("Loading default commands...")
    logger.info("Logging level: {}".format(loglevel))
    logger.debug("Say hi!")
    application.add_handlers([CommandHandler("start", start_cmd)])
    application.run_polling()
