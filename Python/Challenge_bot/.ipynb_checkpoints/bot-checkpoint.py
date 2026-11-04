import os
import json
from datetime import datetime, timezone

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

QUESTIONS = [
    "How many minutes of free time did you have today?",
    "What activity did you plan to do during your free time?",
    "What activity did you actually do?",
    "How many minutes did you spend on that activity?",
    "Did you use your smartphone during that activity? (yes/no)",
]


async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    context.user_data["answers"] = []
    context.user_data["question_index"] = 0

    await update.message.reply_text(
        "Welcome to the Free-Time Research Questionnaire.\n"
        "Please answer each question in turn.\n\n"
        "Question 1: " + QUESTIONS[0]
    )


async def receive_answer(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if "question_index" not in context.user_data:
        await update.message.reply_text(
            "Please type /start to begin the questionnaire."
        )
        return

    answer = update.message.text.strip()

    if not answer:
        await update.message.reply_text("Please enter an answer.")
        return

    index = context.user_data["question_index"]
    context.user_data["answers"].append(answer)
    index += 1

    if index < len(QUESTIONS):
        context.user_data["question_index"] = index
        await update.message.reply_text(
            f"Question {index + 1}: {QUESTIONS[index]}"
        )
        return

    record = {
        # Telegram ID is used here for demonstration.
        # Research deployments should use a pseudonymous participant ID.
        "telegram_user_id": update.effective_user.id,
        "submitted_at": datetime.now(timezone.utc).isoformat(),
        "answers": dict(zip(QUESTIONS, context.user_data["answers"])),
    }

    with open("responses.jsonl", "a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False) + "\n")

    context.user_data.pop("answers", None)
    context.user_data.pop("question_index", None)

    await update.message.reply_text(
        "Thank you! Your questionnaire has been recorded."
    )


async def cancel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    context.user_data.pop("answers", None)
    context.user_data.pop("question_index", None)

    await update.message.reply_text(
        "Questionnaire cancelled. Type /start to begin again."
    )


def main():
    TELEGRAM_BOT_TOKEN = 8788642319:AAEIzJ5Rk95Qt3uqp0TjiG8BoJ7j5SQDlFc
    token = os.getenv("TELEGRAM_BOT_TOKEN")

    if not token:
        raise RuntimeError(
            "Please set the TELEGRAM_BOT_TOKEN environment variable."
        )

    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, receive_answer)
    )

    print("Questionnaire bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()