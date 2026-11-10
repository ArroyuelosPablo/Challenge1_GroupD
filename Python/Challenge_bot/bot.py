import os
import logging
from collections import Counter
from datetime import date, datetime, timedelta, time, timezone
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
SURVEY_TIME = time(0, 5, tzinfo=TZ)
SURVEY_TIME_LABEL = f"{SURVEY_TIME:%H:%M} Europe/Amsterdam time"

MINIMUM_SURVEY_SECONDS = 600
DEMO_PERIOD_START = date(2026, 11, 6)
DEMO_PERIOD_END = date(2026, 11, 10)
DEMO_SEND_DATE = date(2026, 11, 12)

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
    Group unmerged sessions shorter than 10 minutes by package and
    activity date. Calculate durations from timestamps to avoid
    errors caused by truncated duration_seconds values.

    Original usage records are preserved and linked to the merged row.
    """

    # Retrieve all unmerged sessions for the requested activity date.
    result = (
        supabase.table("usage_sessions")
        .select(
            "id, app_name, package_name, start_time, end_time, duration_seconds"
        )
        .eq("activity_date", str(survey_date))
        .is_("merged_id", "null")
        .execute()
    )

    groups: dict[str, list[dict]] = {}

    for item in result.data or []:
        package = item.get("package_name")

        if not package:
            continue

        start = parse_timestamp(item["start_time"])
        end = parse_timestamp(item["end_time"])

        actual_duration = (end - start).total_seconds()

        # Ignore invalid or zero-length sessions.
        if actual_duration <= 0:
            logger.warning(
                "Skipping invalid session %s: duration %.3f seconds",
                item["id"],
                actual_duration,
            )
            continue

        # Only sessions shorter than 600 seconds are merged.
        if actual_duration < MINIMUM_SURVEY_SECONDS:
            item["_actual_duration"] = actual_duration
            groups.setdefault(package, []).append(item)

    logger.info(
        "Merging for %s: %d short sessions in %d packages",
        survey_date,
        sum(len(items) for items in groups.values()),
        len(groups),
    )

    for package, items in groups.items():

        total_duration = sum(
            item["_actual_duration"] for item in items
        )

        logger.info(
            "%s: %d sessions, %.3f actual seconds total",
            package,
            len(items),
            total_duration,
        )

        starts = [
            parse_timestamp(item["start_time"])
            for item in items
        ]

        ends = [
            parse_timestamp(item["end_time"])
            for item in items
        ]

        # The database requires duration_seconds > 0.
        # Keep the integer duration below 600 when the actual
        # combined duration is below 600 seconds.
        stored_duration = max(1, int(total_duration))

        row = {
            "app_name": next(
                (
                    item["app_name"]
                    for item in items
                    if item.get("app_name")
                ),
                package,
            ),
            "package_name": package,

            # activity_date is generated by Supabase.
            "start_time": min(starts)
                .astimezone(timezone.utc)
                .isoformat(),

            "end_time": max(ends)
                .astimezone(timezone.utc)
                .isoformat(),

            "duration_seconds": stored_duration,
            "session_count": len(items),
        }

        # Insert the merged record.
        saved = (
            supabase.table("usage_merged")
            .insert(row)
            .execute()
        )

        if not saved.data:
            raise RuntimeError(
                f"Could not retrieve merged row for {package}"
            )

        merged_id = saved.data[0]["id"]

        # Link the original sessions to the merged record.
        (
            supabase.table("usage_sessions")
            .update({"merged_id": merged_id})
            .in_("id", [item["id"] for item in items])
            .execute()
        )

        logger.info(
            "Successfully merged %d sessions for %s. "
            "Merged ID: %s, duration: %d seconds",
            len(items),
            package,
            merged_id,
            stored_duration,
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

# ---------------------------------------------------------------------
# Weekly statistics
# ---------------------------------------------------------------------

# INSERT THE FOUR NEW WEEKLY STATISTICS FUNCTIONS HERE:
# 1. fetch_all_supabase_rows()
# 2. calculate_weekly_stats(week_start)
# 3. save_weekly_stats(stats)
# 4. generate_and_save_weekly_stats(week_start)

def fetch_all_supabase_rows(table_name, columns, filters=None):
    """
    Retrieve all matching rows in batches of 1000.
    Avoids relying on Supabase's default result limit.
    """
    rows = []
    offset = 0
    page_size = 1000

    while True:
        query = supabase.table(table_name).select(columns)

        if filters:
            for filter_type, column, value in filters:
                if filter_type == "eq":
                    query = query.eq(column, value)
                elif filter_type == "gte":
                    query = query.gte(column, value)
                elif filter_type == "lte":
                    query = query.lte(column, value)

        result = (
            query
            .range(offset, offset + page_size - 1)
            .execute()
        )

        batch = result.data or []
        rows.extend(batch)

        if len(batch) < page_size:
            break

        offset += page_size

    return rows


def most_common_response_value(responses: list[dict], field: str) -> str | None:
    """Return the most common non-empty string, with stable tie-breaking."""
    counts = Counter()
    for response in responses:
        value = response.get(field)
        if isinstance(value, str) and value.strip():
            counts[value.strip()] += 1

    if not counts:
        return None

    return min(counts, key=lambda value: (-counts[value], value.casefold()))


def build_reflection_prompt(
    top_vulnerable_context: str | None,
    mismatch_count: int,
    response_count: int,
    period_label: str = "this week",
) -> str:
    """Create a neutral reflection prompt from the reporting-period data."""
    if response_count == 0:
        return (
            f"I don't have completed check-ins for {period_label}. Would a shorter "
            "check-in make it easier to reflect on how your plans and phone use fit together?"
        )

    if mismatch_count == 0:
        return (
            "I noticed your phone use generally matched your intention in this "
            f"period's completed check-ins ({period_label}). "
            "What helped that alignment feel possible?"
        )

    period_phrase = (
        "this week"
        if period_label == "this week"
        else f"during {period_label}"
    )
    if top_vulnerable_context:
        context_phrases = {
            "short_break": "during short breaks",
            "before_bed": "before bed",
            "waiting": "while waiting",
            "after_study": "after studying",
            "bored": "when you felt bored",
            "avoiding_task": "when avoiding a task",
            "notification": "after receiving a notification",
            "other": "in other situations",
        }
        readable_context = context_phrases.get(
            top_vulnerable_context,
            f"in the context '{top_vulnerable_context.replace('_', ' ')}'",
        )
        observed_period = (
            period_phrase
            if period_label == "this week"
            else f"in the {period_label} check-ins"
        )
        return (
            "I noticed your unplanned screen time was most often reported "
            f"{readable_context} {observed_period}. Was that a necessary way to wind down, "
            "or something you'd like to adjust?"
        )

    return (
        f"I noticed some moments when your plans changed {period_phrase}. What was "
        "happening around those moments, and how did the phone use fit into them?"
    )


def build_weekly_message(
    insights: dict,
    period_label: str = "this week",
) -> str:
    """Format stored insights as a concise, narrative Telegram message."""
    intention_match_rate = insights["intention_match_rate"]
    reflection_prompt = insights["reflection_prompt"]
    is_weekly_summary = period_label == "this week"
    summary_title = (
        "weekly summary"
        if is_weekly_summary
        else f"summary for {period_label}"
    )
    period_lead = (
        "This week"
        if is_weekly_summary
        else f"During {period_label}"
    )

    if intention_match_rate is None:
        return (
            f"Hi! Here is your {summary_title}.\n\n"
            f"There were no completed check-ins for {period_label}, "
            "so I can't compare "
            "your intentions with how you used your phone.\n\n"
            f"{reflection_prompt}"
        )

    percentage = f"{intention_match_rate * 100:.1f}".rstrip("0").rstrip(".")
    vulnerable_contexts = insights["vulnerable_contexts"]

    if not vulnerable_contexts and intention_match_rate == 1:
        return (
            f"Hi! Here is your {summary_title}.\n\n"
            f"{period_lead}, you maintained your original intention in "
            f"{percentage}% of your sessions.\n\n"
            "No changes between intention and phone use were reported in your "
            "completed sessions.\n\n"
            f"{reflection_prompt}"
        )

    top_vulnerable_context = (
        vulnerable_contexts[0]["context"]
        if vulnerable_contexts
        else None
    )
    if top_vulnerable_context:
        top_vulnerable_context = top_vulnerable_context.replace("_", " ")
    else:
        top_vulnerable_context = "not recorded"

    most_displaced_intention = (
        insights["most_displaced_intention"] or "not recorded"
    )
    frequent_actual_activity = (
        insights["frequent_actual_activity"] or "not recorded"
    )

    return f"""Hi! Here is your {summary_title}.

{period_lead}, you maintained your original intention in {percentage}% of your sessions.

Among the moments when your plans changed, you most often postponed '{most_displaced_intention}' and reported '{frequent_actual_activity}' as the activity you did instead. These changes were most often reported in the context '{top_vulnerable_context}'.

{reflection_prompt}"""


def calculate_weekly_stats(week_start, week_end=None):
    """
    Calculate usage and questionnaire statistics for a date range.

    With no week_end, week_start must be a Monday and the complete
    Monday-through-Sunday week is calculated. Supplying week_end allows
    a custom inclusive date range for one-off partial-period summaries.
    """

    if isinstance(week_start, str):
        week_start = datetime.strptime(
            week_start, "%Y-%m-%d"
        ).date()

    if week_end is None:
        if week_start.weekday() != 0:
            raise ValueError("week_start must be a Monday.")
        week_end = week_start + timedelta(days=6)
        period_label = "this week"
    else:
        if isinstance(week_end, str):
            week_end = datetime.strptime(
                week_end, "%Y-%m-%d"
            ).date()
        if week_end < week_start:
            raise ValueError("week_end must be on or after week_start.")
        period_label = (
            f"{week_start.strftime('%B')} {week_start.day}-"
            f"{week_end.day}"
            if week_start.month == week_end.month
            else (
                f"{week_start.strftime('%B')} {week_start.day} to "
                f"{week_end.strftime('%B')} {week_end.day}"
            )
        )

    logger.info(
        "Calculating reporting-period statistics from %s to %s",
        week_start,
        week_end,
    )

    # ---------------------------------------------------------
    # 1. Retrieve original usage sessions for the week
    # ---------------------------------------------------------

    sessions = fetch_all_supabase_rows(
        "usage_sessions",
        "id, app_name, package_name, start_time, end_time, merged_id, activity_date",
        [
            ("gte", "activity_date", str(week_start)),
            ("lte", "activity_date", str(week_end)),
        ],
    )

    total_usage = 0.0
    app_usage = Counter()
    session_durations = {}
    merged_durations = {}

    valid_sessions = []

    for session in sessions:
        try:
            start = parse_timestamp(session["start_time"])
            end = parse_timestamp(session["end_time"])

            duration = (end - start).total_seconds()

            if duration <= 0:
                logger.warning(
                    "Ignoring invalid session %s",
                    session["id"],
                )
                continue

            session_id = session["id"]
            session_durations[session_id] = duration

            total_usage += duration

            app_name = (
                session.get("app_name")
                or session.get("package_name")
                or "Unknown"
            )

            app_usage[app_name] += duration

            merged_id = session.get("merged_id")

            if merged_id:
                merged_durations[merged_id] = (
                    merged_durations.get(merged_id, 0.0)
                    + duration
                )

            valid_sessions.append(session)

        except (KeyError, TypeError, ValueError) as exc:
            logger.warning(
                "Could not calculate duration for session %s: %s",
                session.get("id"),
                exc,
            )

    total_sessions = len(valid_sessions)

    # ---------------------------------------------------------
    # 2. Retrieve completed questionnaire responses
    # ---------------------------------------------------------

    responses = fetch_all_supabase_rows(
        "activity_responses",
        "session_id, merged_id, intended_activity, actual_activity, "
        "context, reason, matched_intention, completed",
        [
            ("eq", "completed", True),
        ],
    )

    session_ids = set(session_durations)
    merged_ids = set(merged_durations)

    completed_responses = []

    for response in responses:
        session_id = response.get("session_id")
        merged_id = response.get("merged_id")

        # Include only responses associated with this week's
        # original sessions or their merged targets.
        if session_id in session_ids or merged_id in merged_ids:
            completed_responses.append(response)

    # ---------------------------------------------------------
    # 3. Calculate intention alignment and mismatch patterns
    # ---------------------------------------------------------

    valid_responses = [
        response
        for response in completed_responses
        if isinstance(response.get("matched_intention"), bool)
    ]
    mismatch_responses = [
        response
        for response in valid_responses
        if response.get("matched_intention") is False
    ]
    mismatch_count = len(mismatch_responses)
    displaced_seconds = 0.0

    for response in mismatch_responses:
        session_id = response.get("session_id")
        merged_id = response.get("merged_id")

        if session_id:
            displaced_seconds += session_durations.get(session_id, 0.0)
        elif merged_id:
            displaced_seconds += merged_durations.get(merged_id, 0.0)

    match_count = sum(
        response.get("matched_intention") is True
        for response in valid_responses
    )
    intention_match_rate = (
        match_count / len(valid_responses)
        if valid_responses
        else None
    )

    context_counts = Counter(
        response["context"]
        for response in mismatch_responses
        if isinstance(response.get("context"), str)
        and response["context"].strip()
    )
    vulnerable_contexts = [
        {"context": context, "mismatch_count": count}
        for context, count in sorted(
            context_counts.items(),
            key=lambda item: (-item[1], item[0].casefold()),
        )
    ]
    top_vulnerable_context = (
        vulnerable_contexts[0]["context"]
        if vulnerable_contexts
        else None
    )
    most_displaced_intention = most_common_response_value(
        mismatch_responses,
        "intended_activity",
    )
    frequent_actual_activity = most_common_response_value(
        mismatch_responses,
        "actual_activity",
    )

    # ---------------------------------------------------------
    # 5. Build the JSON insights field
    # ---------------------------------------------------------

    insights = {
        "intention_match_rate": (
            round(intention_match_rate, 4)
            if intention_match_rate is not None
            else None
        ),
        "vulnerable_contexts": vulnerable_contexts,
        "most_displaced_intention": most_displaced_intention,
        "frequent_actual_activity": frequent_actual_activity,
        "reflection_prompt": build_reflection_prompt(
            top_vulnerable_context,
            mismatch_count,
            len(valid_responses),
            period_label,
        ),
    }

    # ---------------------------------------------------------
    # 6. Return values matching weekly_stats table columns
    # ---------------------------------------------------------

    stats = {
        "week_start": str(week_start),
        "total_sessions": total_sessions,
        "phone_use_seconds": round(total_usage),
        "mismatch_count": mismatch_count,
        "displaced_seconds": round(displaced_seconds),
        "insights": insights,
    }

    logger.info("Weekly statistics calculated: %s", stats)

    return stats


def save_weekly_stats(stats):
    """
    Insert or update weekly statistics.
    week_start is unique in the Supabase table.
    """

    result = (
        supabase.table("weekly_stats")
        .upsert(
            stats,
            on_conflict="week_start",
        )
        .execute()
    )

    if not result.data:
        raise RuntimeError(
            "Weekly statistics could not be saved to Supabase."
        )

    logger.info(
        "Weekly statistics successfully saved for %s",
        stats["week_start"],
    )

    return result.data[0]


def generate_and_save_weekly_stats(week_start):
    """
    Calculate and save weekly statistics in one operation.
    """

    stats = calculate_weekly_stats(week_start)
    saved_record = save_weekly_stats(stats)

    return saved_record


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


async def send_demo_summary(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    """Send the one-time November 6-10 demo summary on November 12."""
    chat = update.effective_chat
    message = update.effective_message
    if chat is None or chat.id != CHAT_ID or message is None:
        return ConversationHandler.END

    if datetime.now(TZ).date() != DEMO_SEND_DATE:
        await message.reply_text(
            "This one-time demo summary can be sent on November 12, 2026."
        )
        return ConversationHandler.END

    if context.application.bot_data.get("demo_summary_sent"):
        await message.reply_text(
            "The one-time demo summary has already been sent."
        )
        return ConversationHandler.END

    try:
        stats = calculate_weekly_stats(
            DEMO_PERIOD_START,
            DEMO_PERIOD_END,
        )
        await context.bot.send_message(
            chat_id=CHAT_ID,
            text=build_weekly_message(
                stats["insights"],
                period_label="November 6-10",
            ),
        )
    except Exception:
        logger.exception("Failed to send the one-time demo summary")
        await message.reply_text(
            "I couldn't generate or send the demo summary. "
            "The error was logged; please try again."
        )
        return ConversationHandler.END

    context.application.bot_data["demo_summary_sent"] = True
    logger.info(
        "One-time demo summary sent for %s through %s",
        DEMO_PERIOD_START,
        DEMO_PERIOD_END,
    )
    return ConversationHandler.END


async def generate_weekly_stats_job(
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Generate and save statistics for the previous week."""

    today = datetime.now(TZ).date()

    # Find the Monday of the previous week.
    this_monday = today - timedelta(days=today.weekday())
    last_week_start = this_monday - timedelta(days=7)

    try:
        saved_record = generate_and_save_weekly_stats(last_week_start)
    except Exception:
        logger.exception("Failed to generate weekly statistics")
        return

    logger.info(
        "Weekly statistics successfully saved for %s",
        last_week_start,
    )

    try:
        insights = saved_record.get("insights")
        if not isinstance(insights, dict):
            raise RuntimeError(
                f"Saved weekly statistics for {last_week_start} "
                "do not contain a valid insights object."
            )

        await context.bot.send_message(
            chat_id=CHAT_ID,
            text=build_weekly_message(insights),
        )
    except Exception:
        logger.exception(
            "Failed to send the weekly summary for %s",
            last_week_start,
        )

# ---------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------


def main() -> None:
    app = Application.builder().token(BOT_TOKEN).build()

    # Daily questionnaire
    app.job_queue.run_daily(
        schedule_daily_survey,
        time=SURVEY_TIME,
        name="daily_session_survey",
    )
    # Existing weekly report schedule: Tuesday at 00:15 Europe/Amsterdam.
    app.job_queue.run_daily(
        generate_weekly_stats_job,
        time=time(hour=0, minute=15, tzinfo=TZ),
        days=(2,),
        name="weekly_statistics",
        )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CommandHandler("retry", retry_save))
    app.add_handler(CommandHandler("send_demo_summary", send_demo_summary))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, receive_answer))

    app.run_polling()


if __name__ == "__main__":
    main()