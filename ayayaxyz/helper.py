from typing import Any
from telegram import Message, InlineKeyboardButton
from telegram.ext import Application, CallbackQueryHandler
import secrets


async def reply_status(message: Message, text: str, silent=False, **kwargs):
    text = """<b>Status:</b>
{}""".format(
        text
    ).strip()
    return await reply_html(message=message, text=text, silent=silent, **kwargs)


async def reply_error(message: Message, text: str, silent=False, **kwargs):
    text = """<b>Error:</b>
{}""".format(
        text
    ).strip()
    return await reply_html(message=message, text=text, silent=silent, **kwargs)


async def edit_status(message: Message, text: str, **kwargs):
    text = """<b>Status:</b>
{}""".format(
        text
    ).strip()
    return await edit_html(message=message, text=text, **kwargs)


async def edit_error(message: Message, text: str, **kwargs):
    text = """<b>Error:</b>
{}""".format(
        text
    ).strip()
    return await edit_html(message=message, text=text, **kwargs)


async def reply_html(message: Message, text: str, silent=False, **kwargs):
    """Reply message with Telegram HTML content

    + `message`: a telegram.Message object
    + `text`: a HTML string to be sent
    """
    return await message.reply_html(
        text=text, disable_web_page_preview=True, disable_notification=silent, **kwargs
    )


async def edit_html(message: Message, text: str, **kwargs):
    return await message.edit_text(
        text=text, parse_mode="HTML", disable_web_page_preview=True, **kwargs
    )


def button_build(button: tuple[str, Any, str, str], application: Application):
    try:
        type = button[3]
    except (IndexError, ValueError):
        type = "callback"
    if not type:
        type = "callback"
    id = secrets.token_urlsafe(16)
    pattern = button[2].format(id=id)
    match type:
        case "callback":
            application.add_handler(
                CallbackQueryHandler(
                    button[1],
                    pattern,
                )
            )
            button = InlineKeyboardButton(
                button[0],
                callback_data=pattern,
            )
        case "url":
            button = InlineKeyboardButton(
                button[0],
                url=pattern,
            )
        case _:
            raise RuntimeError("Unknown type")
    return button


def buttons_build(
    button_list: list[list[tuple[str, Any, str, str]]],
    application: Application,
    base: list[list[InlineKeyboardButton]] = None,
) -> list[list[InlineKeyboardButton]]:
    if base is None:
        base = []
    btn = base
    for button_row in button_list:
        btn_row = []
        for button in button_row:
            btn_row.append(button_build(button, application=application))
        btn.append(btn_row)
    return btn
