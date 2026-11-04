import os
import logging
from datetime import datetime, timedelta, time, timezone
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from supabase import create_client
from telegram import (
    Update,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    ConversationHandler,
    filters,
)

# ---------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------

load_dotenv()

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = int(os.environ["TELEGRAM_USER_ID"])

supabase = create_client(
    os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_KEY"],
)

TZ = ZoneInfo("Europe/Amsterdam")
ASKING = 1

# Daily survey time (Europe/Amsterdam). Change it here only.
SURVEY_TIME = time(1, 29, tzinfo=TZ)
SURVEY_TIME_LABEL = f"{SURVEY_TIME:%H:%M} Europe/Amsterdam time"

MINIMUM_SURVEY_SECONDS = 600

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def describe_supabase_error(exc: Exception) -> str:
    """Return a useful, non-secret error description for logs/chat."""
    parts = [str(exc).strip() or exc.__class__.__name__]
    for attr in ("message", "details", "hint", "code"):
        value = getattr(exc, attr, None)
        if value and str(value) not in parts:
            parts.append(f"{attr}: {value}")
    return " | ".join(parts)[:1200]


QUESTIONS = [
    "What did you originally intend to do during this time?",
    "What did you actually end up doing with your phone during this time?",
    "Why did you choose to use your phone during this time?",
    "Give a short explanation as to why you chose to use your phone.",
    "Did your actual activity match your intended activity? Please answer Yes or No.",
]

ANSWER_OPTIONS = {
    0: [
        "study",
        "chores",
        "exercise",
        "rest",
        "social",
        "work",
        "hobby",
        "other",
    ],
    1: [
        "entertainment",
        "study",
        "messaging",
        "scrolling",
        "news",
        "other",
    ],
    2: [
        "short_break",
        "waiting",
        "after_study",
        "before_bed",
        "bored",
        "avoiding_task",
        "notification",
        "other",
    ],
}

# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------


def parse_timestamp(value: str) -> datetime:
    """Parse an ISO timestamp; assume Amsterdam time if timezone is absent."""
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ)
    return dt


def make_keyboard(question_index: int) -> ReplyKeyboardMarkup | None:
    """Return an answer keyboard for choice questions; Q4 uses free text."""
    options = ANSWER_OPTIONS.get(question_index)
    if not options:
        return None

    return ReplyKeyboardMarkup(
        [[option] for option in options],
        resize_keyboard=True,
        one_time_keyboard=False,
        is_persistent=True,
    )


def prepare_merged_sessions(survey_date) -> None:
    """
    Group every unmerged sub-10-minute session by package and insert one
    usage_merged row per group, then link the sessions to it.

    Meant to run once per finished day. The merged_id IS NULL filter keeps
    reruns safe: sessions that are already linked are skipped, so no
    duplicate merged rows are created.
    """
    result = (
        supabase.table("usage_sessions")
        .select("id, app_name, package_name, start_time, end_time, duration_seconds")
        .eq("activity_date", str(survey_date))
        .lt("duration_seconds", MINIMUM_SURVEY_SECONDS)
        .is_("merged_id", "null")
        .execute()
    )

    groups: dict[str, list[dict]] = {}
    for item in result.data or []:
        package = item.get("package_name")
        if package:
            groups.setdefault(package, []).append(item)

    logger.info(
        "Merging for %s: %d unmerged short sessions in %d packages",
        survey_date,
        len(result.data or []),
        len(groups),
    )
    for package, items in groups.items():
        logger.info(
            "  %s: %d sessions, %d seconds total",
            package,
            len(items),
            sum(int(x["duration_seconds"]) for x in items),
        )

    for package, items in groups.items():
        starts = [parse_timestamp(x["start_time"]) for x in items]
        ends = [parse_timestamp(x["end_time"]) for x in items]
        row = {
            "app_name": next(
                (x["app_name"] for x in items if x.get("app_name")), package
            ),
            "package_name": package,
            # activity_date is a generated column in usage_merged, so the
            # database fills it in from start_time. Do not insert it.
            "start_time": min(starts).astimezone(timezone.utc).isoformat(),
            "end_time": max(ends).astimezone(timezone.utc).isoformat(),
            "duration_seconds": sum(int(x["duration_seconds"]) for x in items),
            "session_count": len(items),
        }

        saved = supabase.table("usage_merged").insert(row).execute()
        if not saved.data:
            raise RuntimeError(f"Could not retrieve merged row for {package}")

        merged_id = saved.data[0]["id"]
        (
            supabase.table("usage_sessions")
            .update({"merged_id": merged_id})
            .in_("id", [x["id"] for x in items])
            .execute()
        )


def get_eligible_survey_targets(survey_date) -> list[dict]:
    """Return individual sessions and qualifying merged groups."""
    prepare_merged_sessions(survey_date)

    individual = (
        supabase.table("usage_sessions")
        .select("id, app_name, package_name, start_time, end_time, duration_seconds")
        .eq("activity_date", str(survey_date))
        .is_("merged_id", "null")
        .gte("duration_seconds", MINIMUM_SURVEY_SECONDS)
        .execute()
    )
    merged = (
        supabase.table("usage_merged")
        .select(
            "id, app_name, package_name, start_time, end_time, "
            "duration_seconds, session_count"
        )
        .eq("activity_date", str(survey_date))
        # Filter on the total duration directly instead of relying on a
        # "qualifies" column that may not be populated on insert.
        .gte("duration_seconds", MINIMUM_SURVEY_SECONDS)
        .execute()
    )
    logger.info(
        "Survey targets for %s: %d individual, %d merged",
        survey_date,
        len(individual.data or []),
        len(merged.data or []),
    )

    targets = [{**x, "target_type": "individual"} for x in (individual.data or [])]
    targets.extend({**x, "target_type": "merged"} for x in (merged.data or []))
    targets.sort(key=lambda x: x["start_time"])
    return targets


def build_response_row(state: dict) -> dict:
    """Map answers and set exactly one target foreign key."""
    session = state["sessions"][state["session_index"]]
    answers = state["answers"]
    row = {
        "intended_activity": answers[0],
        "actual_activity": answers[1],
        "context": answers[2],
        "reason": answers[3],
        "matched_intention": answers[4],
        "response_time": datetime.now(timezone.utc).isoformat(),
        "completed": True,
    }

    if session["target_type"] == "individual":
        row["session_id"] = session["id"]
        row["merged_id"] = None
    else:
        row["session_id"] = None
        row["merged_id"] = session["id"]
    return row


def save_response_to_supabase(state: dict) -> None:
    """Save against either session_id or merged_id."""
    row = build_response_row(state)
    conflict = "session_id" if row["session_id"] is not None else "merged_id"
    supabase.table("activity_responses").upsert(row, on_conflict=conflict).execute()


async def ask_current_question(
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    """Send session information when needed, then ask the current question."""
    state = context.application.bot_data.get("survey")
    if not state:
        return ConversationHandler.END

    session = state["sessions"][state["session_index"]]
    question_index = state["question_index"]

    if question_index == 0:
        start = parse_timestamp(session["start_time"])
        stop = parse_timestamp(session["end_time"])
        duration_minutes = int(session["duration_seconds"]) / 60
        if session["target_type"] == "merged":
            target_label = (
                f"Merged app activity ({session.get('session_count', 0)} "
                "short sessions)"
            )
        else:
            target_label = "Individual session"

        await context.bot.send_message(
            chat_id=CHAT_ID,
            text=(
                f"Survey {state['session_index'] + 1} "
                f"of {len(state['sessions'])}\n"
                f"Activity: {session.get('app_name') or 'Unknown'}\n"
                f"Type: {target_label}\n"
                f"Start: {start.astimezone(TZ):%Y-%m-%d %H:%M}\n"
                f"Stop: {stop.astimezone(TZ):%Y-%m-%d %H:%M}\n"
                f"Total duration: {duration_minutes:.1f} minutes\n\n"
                "Please answer the following questions about this activity."
            ),
        )

    header = f"Question {question_index + 1} of {len(QUESTIONS)}\n\n"

    if question_index == 3:
        # Free-text explanation.
        question_text = (
            f"{header}{QUESTIONS[question_index]}\n\n"
            "Please type a short explanation in your own words."
        )
        reply_markup = ReplyKeyboardRemove()
    elif question_index == 4:
        # Yes/No buttons.
        question_text = f"{header}{QUESTIONS[question_index]}"
        reply_markup = ReplyKeyboardMarkup(
            [["Yes", "No"]],
            one_time_keyboard=True,
            resize_keyboard=True,
        )
    else:
        # Questions 1-3: multiple choice.
        question_text = f"{header}{QUESTIONS[question_index]}"
        reply_markup = make_keyboard(question_index)

    await context.bot.send_message(
        chat_id=CHAT_ID,
        text=question_text,
        reply_markup=reply_markup,
    )

    return ASKING


async def advance_after_save(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    state: dict,
) -> int:
    """Move to the next survey target, or finish the survey."""
    state.pop("save_pending", None)
    state["session_index"] += 1

    if state["session_index"] >= len(state["sessions"]):
        context.application.bot_data.pop("survey", None)
        await update.effective_message.reply_text(
            "Thank you! All eligible session questionnaires are complete.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return ConversationHandler.END

    state["question_index"] = 0
    state["answers"] = {}

    await update.effective_message.reply_text("Response saved.")
    return await ask_current_question(context)


async def save_current_response(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    """Save a completed response, or retain it for /retry if saving fails."""
    state = context.application.bot_data.get("survey")
    if not state:
        return ConversationHandler.END

    try:
        save_response_to_supabase(state)
    except Exception as exc:
        logger.exception(
            "Could not save survey response to Supabase: %s",
            describe_supabase_error(exc),
        )
        state["save_pending"] = True
        await update.effective_message.reply_text(
            "I couldn't save this response. Your answers are still held in "
            "the bot's memory. Send /retry to try saving again."
        )
        return ASKING

    return await advance_after_save(update, context, state)


# ---------------------------------------------------------------------
# Scheduled survey
# ---------------------------------------------------------------------


async def schedule_daily_survey(
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Survey eligible individual sessions and merged short-session groups."""
    if context.application.bot_data.get("survey"):
        await context.bot.send_message(
            chat_id=CHAT_ID,
            text=(
                "A previous survey is still in progress. "
                "Please finish or cancel it before starting another."
            ),
        )
        return

    survey_date = datetime.now(TZ).date() - timedelta(days=1)

    try:
        eligible_sessions = get_eligible_survey_targets(survey_date)
    except Exception as exc:
        logger.exception(
            "Could not prepare/retrieve survey targets: %s",
            describe_supabase_error(exc),
        )
        await context.bot.send_message(
            chat_id=CHAT_ID,
            text=(
                "I couldn't prepare or retrieve usage sessions from Supabase.\n\n"
                f"Error: {describe_supabase_error(exc)}"
            ),
        )
        return

    if not eligible_sessions:
        await context.bot.send_message(
            chat_id=CHAT_ID,
            text=f"No eligible survey targets found for {survey_date}.",
        )
        return

    context.application.bot_data["survey"] = {
        "sessions": eligible_sessions,
        "session_index": 0,
        "question_index": 0,
        "answers": {},
        "survey_date": str(survey_date),
    }

    individual_count = sum(
        1 for x in eligible_sessions if x["target_type"] == "individual"
    )
    merged_count = len(eligible_sessions) - individual_count

    await context.bot.send_message(
        chat_id=CHAT_ID,
        text=(
            f"Good morning Diana! Here is your survey for {survey_date}.\n"
            f"{len(eligible_sessions)} survey targets qualify "
            f"(For sessions greater than or equal to {MINIMUM_SURVEY_SECONDS // 60} minutes).\n"
            f"Individual app sessions: {individual_count}\n"
            f"Merged app groups sessions: {merged_count}\n"
            "Use /cancel to stop the survey."
        ),
    )
    await ask_current_question(context)


# ---------------------------------------------------------------------
# Command and message handlers
# ---------------------------------------------------------------------


async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Explain that the survey starts automatically on its daily schedule."""
    if update.effective_chat.id != CHAT_ID:
        await update.effective_message.reply_text(
            "This bot is not configured for this chat."
        )
        return
    await update.effective_message.reply_text(
        "The daily survey bot is running. The survey is scheduled for "
        f"{SURVEY_TIME_LABEL}. Use /cancel to stop an active survey."
    )


async def receive_answer(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    """Validate and record an answer to the current survey question."""
    message = update.effective_message

    if message is None or update.effective_chat is None:
        return ConversationHandler.END

    if update.effective_chat.id != CHAT_ID:
        return ConversationHandler.END

    state = context.application.bot_data.get("survey")

    if not state:
        await message.reply_text(
            "There is no active survey right now. The next survey is scheduled "
            f"for {SURVEY_TIME_LABEL}."
        )
        return ConversationHandler.END

    if state.get("save_pending"):
        await message.reply_text(
            "Your previous response is waiting to be saved. "
            "Please send /retry first."
        )
        return ASKING

    question_index = state["question_index"]
    answer = (message.text or "").strip()

    if question_index == 3:
        # Question 4: free-text explanation must not be empty.
        if not answer:
            await message.reply_text(
                "Please provide a short explanation. Your answer cannot be empty."
            )
            return ASKING

        matched_answer = answer

    elif question_index == 4:
        # Question 5: must be Yes or No, stored as a Boolean.
        normalized_answer = answer.casefold()

        if normalized_answer not in ("yes", "no"):
            await message.reply_text("Please choose Yes or No using the buttons.")
            return ASKING

        matched_answer = normalized_answer == "yes"

    else:
        # Questions 1-3: validate against the displayed options.
        valid_options = ANSWER_OPTIONS[question_index]

        matched_answer = next(
            (
                option
                for option in valid_options
                if option.casefold() == answer.casefold()
            ),
            None,
        )

        if matched_answer is None:
            await message.reply_text(
                "Please choose one of the displayed answer options."
            )
            await ask_current_question(context)
            return ASKING

    state["answers"][question_index] = matched_answer

    if question_index < len(QUESTIONS) - 1:
        state["question_index"] += 1
        return await ask_current_question(context)

    # Final answer validated: save the response.
    return await save_current_response(update, context)


async def retry_save(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    """Retry saving a completed response that previously failed."""
    if update.effective_chat.id != CHAT_ID:
        return ConversationHandler.END

    state = context.application.bot_data.get("survey")
    if not state or not state.get("save_pending"):
        await update.effective_message.reply_text(
            "There is no completed response waiting to be saved."
        )
        return ASKING if state else ConversationHandler.END

    try:
        save_response_to_supabase(state)
    except Exception as exc:
        logger.exception(
            "Retry failed while saving survey response: %s",
            describe_supabase_error(exc),
        )
        await update.effective_message.reply_text(
            "Saving failed again. Your answers are still in memory. "
            "Please send /retry to try again."
        )
        return ASKING

    return await advance_after_save(update, context, state)


async def cancel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    if update.effective_chat.id != CHAT_ID:
        return ConversationHandler.END

    context.application.bot_data.pop("survey", None)
    await update.effective_message.reply_text(
        "Survey cancelled. Previously saved responses remain in Supabase.",
        reply_markup=ReplyKeyboardRemove(),
    )
    return ConversationHandler.END


# ---------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------


def main() -> None:
    app = Application.builder().token(BOT_TOKEN).build()

    app.job_queue.run_daily(
        schedule_daily_survey,
        time=SURVEY_TIME,
        name="daily_session_survey",
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CommandHandler("retry", retry_save))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, receive_answer))

    app.run_polling()


if __name__ == "__main__":
    main()