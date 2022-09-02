from telegram import Message


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
