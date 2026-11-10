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


def calculate_weekly_stats(week_start):
    """
    Calculate weekly usage and questionnaire statistics.

    week_start must be a Monday.
    Reporting period: Monday through Sunday, Amsterdam local dates.
    """

    if isinstance(week_start, str):
        week_start = datetime.strptime(
            week_start, "%Y-%m-%d"
        ).date()

    if week_start.weekday() != 0:
        raise ValueError("week_start must be a Monday.")

    week_end = week_start + timedelta(days=6)

    logger.info(
        "Calculating weekly statistics from %s to %s",
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
    # 3. Calculate mismatch count and displaced seconds
    # ---------------------------------------------------------

    mismatch_count = 0
    displaced_seconds = 0.0

    intended_activities = Counter()
    actual_activities = Counter()
    reasons = Counter()

    for response in completed_responses:

        intended = response.get("intended_activity")
        actual = response.get("actual_activity")
        reason = response.get("reason")

        if intended:
            intended_activities[intended] += 1

        if actual:
            actual_activities[actual] += 1

        if reason:
            reasons[reason] += 1

        # The bot stores matched_intention as a Boolean.
        if response.get("matched_intention") is False:
            mismatch_count += 1

            session_id = response.get("session_id")
            merged_id = response.get("merged_id")

            if session_id:
                displaced_seconds += session_durations.get(
                    session_id, 0.0
                )

            elif merged_id:
                displaced_seconds += merged_durations.get(
                    merged_id, 0.0
                )

    # ---------------------------------------------------------
    # 4. Calculate percentages and most common activities
    # ---------------------------------------------------------

    completed_count = len(completed_responses)

    match_count = sum(
        1
        for response in completed_responses
        if response.get("matched_intention") is True
    )

    match_percentage = (
        round(match_count / completed_count * 100, 2)
        if completed_count > 0
        else 0.0
    )

    most_used_app = (
        app_usage.most_common(1)[0][0]
        if app_usage else None
    )

    most_common_intended = (
        intended_activities.most_common(1)[0][0]
        if intended_activities else None
    )

    most_common_actual = (
        actual_activities.most_common(1)[0][0]
        if actual_activities else None
    )

    most_common_reason = (
        reasons.most_common(1)[0][0]
        if reasons else None
    )

    # ---------------------------------------------------------
    # 5. Build the JSON insights field
    # ---------------------------------------------------------

    insights = {
        "week_end": str(week_end),
        "completed_surveys": completed_count,
        "matched_intentions": match_count,
        "intention_match_percentage": match_percentage,
        "most_used_app": most_used_app,
        "most_common_intended_activity": most_common_intended,
        "most_common_actual_activity": most_common_actual,
        "most_common_reason": most_common_reason,
        "top_apps": [
            {
                "app_name": app,
                "usage_seconds": round(seconds),
            }
            for app, seconds in app_usage.most_common(5)
        ],
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
