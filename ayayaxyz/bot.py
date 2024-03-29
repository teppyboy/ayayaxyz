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
    DownloadError,
    SearchError,
    LoginError,
)
from saucerer import Saucerer
from saucerer.exceptions import SaucererError
from flask import Flask
from waitress import serve
from threading import Thread

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
_logger = logging.getLogger("ayayaxyz")

app = Flask(__name__)
# app.use_x_sendfile = True
pixiv = Pixiv()
saucerer = Saucerer()
web_url = os.getenv("WEB_URL", "http://127.0.0.1:8080")


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(chat_id=update.effective_chat.id, text="Hi!")


def _pixiv_get_id(context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) == 0:
        return False, "You need to provide either an illustration ID or its url."
    return pixiv.get_id_from_str(context.args[0])


async def _pixiv_dl_illust(
    quick: bool,
    illust,
    pictures: list[int],
    message: telegram.Message,
    to_url: bool = False,
) -> list | dict:
    quality = "original"
    if quick:
        quality = "large"
    try:
        illusts = await pixiv.download_illust(
            illust=illust, pictures=pictures, quality=quality, limit=9, to_url=to_url
        )
    except DownloadError as e:
        msg_kwargs = {
            "message": message,
            "text": "Failed to fetch illustration: <code>{}</code>".format(e),
        }
        _logger.warning("Error while downloading images: {}".format(e))
        return msg_kwargs
    return illusts


def _pixiv_photo_from_str_or_bytes(illust_dls: list, fast: bool = False):
    if fast:
        photo = "{web}/pixiv/raw?url={url}".format(
            web=web_url,
            url=illust_dls[0][0],
        )
        _logger.debug(photo)
    else:
        photo = illust_dls[0][0].getvalue()
    return photo


async def pixiv_id_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    quick: bool = False,
    fast: bool = False,
):
    message = update.effective_message
    get_id = _pixiv_get_id(context=context)
    if not get_id[0]:
        await helper.reply_error(message=message, text=get_id[1])
        return
    illust_id = get_id[1]
    error_buttons = None
    notice_msg_txt = ""
    if fast:
        notice_msg_txt = "\n<b>Note</b>: <code>fid</code> is an experimental implementation and may fail to send images"
    elif not quick:
        notice_msg_txt = "\n<b>Note</b>: <code>qid</code> does the same thing but provides higher performance & stability (in exchange for worse resolution)"
    notice_msg = await helper.reply_status(
        message=message,
        text="""Selected page(s): <code>{selected_images}</code>
Fetching <code>{illust_id}</code>...{notice}""".format(
            selected_images="all"
            if len(context.args) == 1
            else ", ".join([x for x in context.args[1:]]),
            illust_id=illust_id,
            notice=notice_msg_txt,
        ),
        silent=True,
    )
    if fast:

        async def cb_tryid(_: Update, __: CallbackContext):
            return await pixiv_id_cmd(update, context)

        error_buttons = helper.buttons_build(
            [[("Try again with id", cb_tryid, "pixiv-id-tryid-{id}")]],
            application=context.application,
        )
    elif not quick:

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
    to_url = False
    if fast:
        to_url = True

    illusts = await _pixiv_dl_illust(
        quick=quick, illust=illust, pictures=pictures, message=notice_msg, to_url=to_url
    )
    if illusts is dict:
        if not quick:
            illusts.update(
                {"reply_markup": InlineKeyboardMarkup(inline_keyboard=error_buttons)}
            )
        await helper.edit_error(**illusts)
        return
    _logger.debug("Trying to send images bytes...")
    notice = ""
    if quick:
        notice += "\nThis image has low resolution, use <code>id</code>/<code>fid</code> to get higher resolution."
    if len(illusts) > 1:
        notice += "\nUse <code>fid</code>/<code>id</code>/<code>qid</code> with a single page to get the download url."
    caption = "https://www.pixiv.net/en/artworks/{illust_id}{notice}\nTags: {tags}\nTags (translated): {tl_tags}".format(
        illust_id=illust_id,
        notice=notice,
        tags=", ".join(f"<code>{x}</code>" for x in pixiv.get_raw_tags(illust)),
        tl_tags=", ".join(
            f"<code>{x}</code>" for x in pixiv.get_translated_tags(illust)
        ),
    )
    try:
        if len(illusts) == 1:
            dl_button = helper.buttons_build(
                [
                    [
                        (
                            "Download",
                            None,
                            "{web}/pixiv/raw?url={url}".format(
                                web=web_url,
                                url=illusts[0][0]
                                if illusts[0][0] is str
                                else (
                                    await pixiv.get_illust_download_url(
                                        illust=illust, pictures=pictures
                                    )
                                )[0],
                            ),
                            "url",
                        )
                    ]
                ],
                application=context.application,
            )
            photo = _pixiv_photo_from_str_or_bytes(illust_dls=illusts, fast=fast)
            await message.reply_photo(
                photo=photo,
                filename=illusts[0][1],
                caption=caption,
                parse_mode="HTML",
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
                    _logger.debug(x[0])
                    media_url = "{web}/pixiv/raw?url={url}".format(
                        web=web_url,
                        url=x[0],
                    )
                    _logger.debug(media_url)
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
        _logger.warning("Error while sending message: {}".format(e))


async def pixiv_related_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    parent_logger: logging.Logger,
    quick: bool = False,
    tags: list[str] = None,
    sort_popular: bool = False,
    no_related: bool = False,
    translate_tags: bool = True,
    fast: bool = False,
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
                _logger.debug("Tag translation disabled.")
                for arg in _tl_args:
                    tags.remove(arg)
                translate_tags = False
            else:
                translate_tags = True
        if translate_tags:
            try:
                tags = await pixiv.translate_tags(tags=tags)
            except SearchError:
                pass

    _logger.debug("Formatted tags: {}".format(tags))

    notice_txt = ""
    if fast:
        notice_txt = "<code>frelated</code> is an experimental implementation and may fail to send image"
    elif not quick:
        notice_txt = "<code>qrelated</code> is faster & more reliable for worse image quality."
    
    if notice_txt != "":
        notice_txt = "\n<b>Note:</b> " + notice_txt

    notice_msg = await helper.reply_status(
        message=message,
        text="""Searching for image related to <code>{illust_id}</code>{with_tags}...{notice}""".format(
            illust_id=illust_id,
            with_tags=" with tags <code>{}</code>".format(", ".join(tags))
            if tags
            else "",
            notice=notice_txt,
        ),
        silent=True,
    )
    try:
        illust = await pixiv.related_illust(illust_id, tags=tags, recurse=3)
    except SearchError as e:
        await helper.edit_error(
            message=notice_msg,
            text="Failed to search for related image: <code>{}</code>".format(e),
        )
        _logger.warning("Error while searching for related image: {}".format(e))
        return

    illusts = await _pixiv_dl_illust(
        quick=quick, illust=illust, pictures=[0], message=notice_msg, to_url=fast
    )
    if illusts is dict:
        await helper.edit_error(**illusts)
        return

    _logger.debug("Trying to send images bytes...")
    search_row = []
    if tags:

        async def cb_next(_: Update, __: CallbackContext):
            if sort_popular:
                tags_orig.append("-P")
            if no_related:
                tags_orig.append("--no-related")
            _logger.debug(type(tags_orig))
            clone_context = copy(context)
            clone_context.args = ",".join(tags_orig).split(" ")
            return await pixiv_search_cmd(
                update=update,
                context=clone_context,
                parent_logger=parent_logger,
                quick=quick,
                translate_tags=translate_tags,
                fast=fast,
            )

        search_row.append(("Next", cb_next, "pixiv-search-cb-next-{id}"))

    async def cb_related(_: Update, __: CallbackContext):
        clone_context = copy(context)
        clone_context.args = [str(illust["id"])]
        return await pixiv_related_cmd(
            update=update,
            context=clone_context,
            parent_logger=parent_logger,
            quick=quick,
            tags=tags_orig,
            sort_popular=sort_popular,
            no_related=no_related,
            translate_tags=translate_tags,
            fast=fast
        )

    search_row.append(("Related", cb_related, "pixiv-search-cb-related-{id}"))

    async def cb_getoriginalres(cb_update: Update, _: CallbackContext):
        clone_context = copy(context)
        clone_context.args = [str(illust["id"])]
        return await pixiv_id_cmd(cb_update, clone_context, fast=True)

    buttons = helper.buttons_build(
        [
            search_row,
            [
                (
                    "All pages",
                    cb_getoriginalres,
                    "pixiv-search-cb-originalimage-{id}",
                ),
                (
                    "Download",
                    None,
                    "{web}/pixiv/raw?url={url}".format(
                        web=web_url,
                        url=(await pixiv.get_illust_download_url(illust=illust))[0],
                    ),
                    "url",
                ),
            ],
        ],
        application=context.application,
    )

    photo = _pixiv_photo_from_str_or_bytes(illust_dls=illusts, fast=fast)
    try:
        await message.reply_photo(
            photo=photo,
            filename=illusts[0][1],
            caption="https://www.pixiv.net/en/artworks/{illust_id}{notice}".format(
                illust_id=illust["id"],
                notice="\nThis image has low resolution, click <i>All pages</i> to get higher resolution"
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
        _logger.warning("Error while sending message: {}".format(e))


async def pixiv_search_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    parent_logger: logging.Logger,
    quick: bool = False,
    translate_tags: bool = None,
    fast: bool = False,
):
    logger = parent_logger.getChild("search")
    message: telegram.Message = update.effective_message
    keyword: str = " ".join(context.args)
    tags: list[str] = [x.strip() for x in keyword.split(",")]
    tags_set: set[str] = set(tags)
    tags_orig: list[str] = copy(tags)
    related: bool = True
    sort_popular: bool = False
    sort: str | None = None
    notice_msg: telegram.Message | None = None
    # Telegram workaround when you type -- in chat
    _help_tags: set[str] = {"-H", "--help", "—help"}.intersection(tags_set)
    _p_tags: set[str] = {"-P", "--popular", "—popular"}.intersection(tags_set)
    _no_related_tags: set[str] = {"--no-related", "—no-related"}.intersection(tags_set)
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
        logger.debug("Popular mode")
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
        }.intersection(tags_set)
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
        except SearchError:
            pass

    notice_txt = ""
    if fast:
        notice_txt = "<code>fsearch</code> is an experimental implementation and may fail to send image"
    elif not quick:
        notice_txt = "<code>qsearch</code> is faster & more reliable for worse image quality."
    
    if notice_txt != "":
        notice_txt = "\n<b>Note:</b> " + notice_txt

    search_txt = "Searching for <code>{keyword}</code>{popular_mode}{no_related}...{notice}".format(
        keyword=", ".join(tags),
        popular_mode=" in popular mode" if sort == "popular_desc" else "",
        no_related=" without searching related image" if not related else "",
        notice=notice_txt,
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
    except SearchError as e:
        await helper.edit_error(
            message=notice_msg,
            text="Failed to search for image: <code>{}</code>".format(e),
        )
        logger.warning("Error while searching for images: {}".format(e))
        return

    illusts = await _pixiv_dl_illust(
        quick=quick, illust=illusts_search, pictures=[0], message=notice_msg, to_url=fast
    )
    if illusts is dict:
        await helper.edit_error(**illusts)
        return

    logger.debug("Generating callback for button...")

    async def cb_next(_: Update, __: CallbackContext):
        return await pixiv_search_cmd(
            update=update,
            context=context,
            parent_logger=parent_logger,
            quick=quick,
            translate_tags=translate_tags,
            fast=fast,
        )

    async def cb_related(_: Update, __: CallbackContext):
        clone_context = copy(context)
        if _p_tags:
            logger.debug("Removing popular tag before calling related...")
            for _arg in _p_tags:
                tags_orig.remove(_arg)
        clone_context.args = [str(illusts_search["id"])]
        return await pixiv_related_cmd(
            update=update,
            context=clone_context,
            parent_logger=parent_logger,
            quick=quick,
            tags=tags_orig,
            sort_popular=sort_popular,
            no_related=not related,
            translate_tags=translate_tags,
            fast=fast,
        )

    async def cb_getoriginalres(cb_update: Update, _: CallbackContext):
        clone_context = copy(context)
        clone_context.args = [str(illusts_search["id"])]
        return await pixiv_id_cmd(cb_update, clone_context, fast=True)

    buttons = helper.buttons_build(
        [
            [
                ("Next", cb_next, "pixiv-search-cb-next-{id}"),
                ("Related", cb_related, "pixiv-search-cb-related-{id}"),
            ],
            [
                (
                    "All pages",
                    cb_getoriginalres,
                    "pixiv-search-cb-originalimage-{id}",
                ),
                (
                    "Download",
                    None,
                    "{web}/pixiv/raw?url={url}".format(
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

    photo = _pixiv_photo_from_str_or_bytes(illust_dls=illusts, fast=fast)
    _logger.debug(photo)
    try:
        await message.reply_photo(
            photo=photo,
            filename=illusts[0][1],
            caption="https://www.pixiv.net/en/artworks/{illust_id}{notice}".format(
                illust_id=illusts_search["id"],
                notice="\nThis image has low resolution, click <i>All pages</i> to get higher resolution"
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
    logger = _logger.getChild("commands.pixiv")
    try:
        command = context.args[0].lower()
        context.args = context.args[1:]
    except (ValueError, KeyError, IndexError):
        await helper.reply_error(message=message, text="Please specify a sub-command.")
    else:
        match command:
            case "id":
                await pixiv_id_cmd(update=update, context=context)
            case "qid":
                await pixiv_id_cmd(update=update, context=context, quick=True)
            case "fid":
                await pixiv_id_cmd(update=update, context=context, fast=True)
            case "search":
                await pixiv_search_cmd(
                    update=update, parent_logger=logger, context=context
                )
            case "qsearch":
                await pixiv_search_cmd(
                    update=update, parent_logger=logger, context=context, quick=True
                )
            case "fsearch":
                await pixiv_search_cmd(
                    update=update, parent_logger=logger, context=context, fast=True
                )
            case "related":
                await pixiv_related_cmd(
                    update=update, context=context, parent_logger=logger
                )
            case "qrelated":
                await pixiv_related_cmd(
                    update=update, context=context, parent_logger=logger, quick=True
                )
            case "qrelated":
                await pixiv_related_cmd(
                    update=update, context=context, parent_logger=logger, fast=True
                )
            case _:
                await helper.reply_error(message=message, text="Invalid sub-command.")


def init_pixiv(application: Application) -> bool:
    try:
        if os.getenv("PIXIV_REFRESH_TOKEN"):
            _logger.info("Logging into Pixiv using refresh token...")
            pixiv.login_token(os.getenv("PIXIV_REFRESH_TOKEN"))
        else:
            _logger.info("Logging into Pixiv using credentials...")
            _logger.warning("It's recommended to use refresh token to login instead.")
            pixiv.login(os.getenv("PIXIV_USERNAME"), os.getenv("PIXIV_PASSWORD"))
    except LoginError as e:
        _logger.error(
            "Logging into Pixiv failed, disabling Pixiv-related feature: {}".format(e)
        )
        return False
    pixiv.flask_api(app=app)
    _logger.info("Loading Pixiv commands...")
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

async def sauce_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    # logger = _logger.getChild("commands.sauce")
    image_url = context.args[0]
    status_msg = await helper.reply_status(message=message, text="Fetching sauce...", silent=True)
    try:
        result = await saucerer.search(image=image_url, hidden=False)
    except SaucererError as e:
        await helper.edit_status(status_msg, f"Failed to fetch sauce: <code>{e}</code>")
        return
    if len(result.sauces) == 0:
        await helper.edit_status(status_msg, f"No sauces were found for this image")
        return
    reply_txt = """<b>Result:</b>\n"""
    for i, sauce in enumerate(result.sauces):
        if not (sauce.illust.id and sauce.illust.url) and len(sauce.misc_info) == 0:
            continue
        first = True
        reply_txt += f"[{i + 1}]: "
        if sauce.illust.id:
            reply_txt += f"<code>{sauce.illust.id}</code>"
            first = False
        if sauce.illust.url:
            if not first:
                reply_txt += ' - '
            else:
                first = False
            reply_txt += f'<a href="{sauce.illust.url}">URL</a>'
        if not first:
            reply_txt += ' - '
        else:
            first = False
        reply_txt += f"{round(sauce.match_percentage * 100, 2)}%\n"
        # reply_txt += f'ID: <code>{sauce.illust.id}</code> - <a href="{sauce.illust.url}">URL</a> - {sauce.match_percentage * 100}%\n'
        if len(sauce.misc_info) > 0:
            reply_txt += "  ▸ Mirror" + ("s" if len(sauce.misc_info) > 1 else "") + ": "
        miscs = []
        for url in sauce.misc_info:
            miscs.append(f'<a href="{url.url}">{url.provider.title()}</a>')
        reply_txt += ", ".join(miscs)
        if len(sauce.misc_info) > 0:
            reply_txt += "\n"
    retry_strs = []
    for v in result.retry_links:
        retry_strs.append(f'<a href="{v.url}">{v.title}</a>')
    reply_txt += f'<b>Retry links:</b> {", ".join(retry_strs)}'
    await helper.edit_html(status_msg, reply_txt)

def main():
    # Initialize task unrelated to Telegram bot itself.
    logging.info("Initializing logging...")
    loglevel = os.getenv("LOGLEVEL", "INFO")
    _logger.setLevel(loglevel)
    application = ApplicationBuilder().token(os.getenv("TOKEN")).build()
    init_pixiv(application=application)
    init_flask()
    _logger.info("Loading default commands...")
    _logger.info("Logging level: {}".format(loglevel))
    _logger.info("Web API Url: {}".format(web_url))
    _logger.debug("Say hi!")
    application.add_handlers([CommandHandler("sauce", sauce_cmd), CommandHandler("start", start_cmd)])
    application.run_polling()
