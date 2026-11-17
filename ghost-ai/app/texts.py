"""User-facing strings sent to interlocutors. RU is default; everything else gets EN."""

RU_LANGS = {"ru", "uk", "be", "kk"}


def pick_lang(language_code: str | None) -> str:
    return "ru" if (language_code or "ru").split("-")[0].lower() in RU_LANGS else "en"


TEXTS = {
    "ru": {
        "notice": (
            "Здравствуйте! Это автоматический AI-ассистент — он отвечает вместо {owner}.\n\n"
            "Чтобы отвечать по делу, мне нужно:\n"
            "• передавать текст нашей переписки в Anthropic Claude (внешний AI-сервис, США);\n"
            "• хранить зашифрованную память о разговоре — {days} дн., потом удаляется.\n\n"
            "Пока вы не согласитесь, ваши сообщения никуда не передаются и не сохраняются. "
            "Согласие можно отозвать в любой момент: /ai_off — отозвать и удалить память, /ai_forget — только удалить память.\n\n"
            "Согласны? Нажмите кнопку или напишите «да» / «нет»."
        ),
        "btn_yes": "✅ Согласен",
        "btn_no": "❌ Не согласен",
        "btn_revoke": "🛑 Отозвать согласие",
        "granted": "Спасибо, согласие записано. Теперь напишите ваше сообщение ещё раз — то, что было до этого, я не сохранял.",
        "declined": "Хорошо, ничего не передаю и не сохраняю. {owner} ответит вам сам.",
        "revoked": "Согласие отозвано, память о нашем разговоре удалена. {owner} ответит вам сам. Чтобы включить ассистента снова — /ai_on.",
        "forgotten": "Готово, память о нашем разговоре удалена.",
        "already": "Это уже учтено.",
        "reask": "Хорошо, спрошу ещё раз.",
    },
    "en": {
        "notice": (
            "Hello! This is an automated AI assistant replying on behalf of {owner}.\n\n"
            "To reply properly I need to:\n"
            "• send the text of our conversation to Anthropic Claude (an external AI service, USA);\n"
            "• keep an encrypted memory of the conversation for {days} days, then it is deleted.\n\n"
            "Until you agree, your messages are not sent anywhere and are not stored. "
            "You can withdraw consent any time: /ai_off — withdraw and delete memory, /ai_forget — only delete memory.\n\n"
            "Do you agree? Tap a button or reply “yes” / “no”."
        ),
        "btn_yes": "✅ I agree",
        "btn_no": "❌ I don't agree",
        "btn_revoke": "🛑 Withdraw consent",
        "granted": "Thank you, consent recorded. Please send your message again — I did not keep anything from before.",
        "declined": "Understood — nothing is sent or stored. {owner} will reply personally.",
        "revoked": "Consent withdrawn and the conversation memory deleted. {owner} will reply personally. Send /ai_on to enable the assistant again.",
        "forgotten": "Done, the conversation memory has been deleted.",
        "already": "Already recorded.",
        "reask": "Okay, I'll ask again.",
    },
}

YES = {"да", "согласен", "согласна", "ок", "окей", "yes", "y", "ok", "okay", "i agree", "agree"}
NO = {"нет", "не согласен", "не согласна", "no", "n", "i don't agree", "disagree"}


def t(lang: str, key: str, **kw) -> str:
    return TEXTS.get(lang, TEXTS["en"])[key].format(**kw)


def parse_answer(text: str) -> str | None:
    norm = "".join(ch for ch in text.lower().strip() if ch.isalnum() or ch in " '’").strip()
    if norm in YES:
        return "y"
    if norm in NO:
        return "n"
    return None
