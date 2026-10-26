"""Read-only attendance quality checks; never determine payroll/absence status."""

from datetime import datetime

NON_PUNCH_STATUSES = frozenset(
    ("On Leave", "Business Trip", "Time Off", "Work From Home", "Holiday")
)


def _as_datetime(value):
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return datetime.fromisoformat(value.strip())
        except ValueError:
            return None
    return None


def review_attendance(attendance, *, calendar_holiday=False, weekly_off=False, current_day=False):
    """Classify attendance evidence without changing the authoritative HRMS status.

    'Missing Punch' means one-sided *expected* punching. Two empty timestamps,
    approved leave and a day with no Attendance document are separate cases.
    None of these review labels triggers deductions.
    """
    result = {
        "punch_case": "",
        "missing_in": 0,
        "missing_out": 0,
        "no_punch_day": 0,
        "unverified_half_day": 0,
        "unrecorded_review_day": 0,
        "working_hours_issue": 0,
        "source_flag_issue": 0,
        "status_punch_conflict": 0,
        "needs_review": 0,
        "gross_presence_hours": 0.0,
        "review_notes": [],
    }
    if attendance is None:
        if calendar_holiday or weekly_off:
            result["punch_case"] = "Off day without record"
        elif current_day:
            result["punch_case"] = "Current day pending"
        else:
            result["punch_case"] = "Day without Attendance record (calendar unverified)"
            result["unrecorded_review_day"] = 1
            result["needs_review"] = 1
            result["review_notes"].append("No Attendance record; verify shift, sync and leave")
        return result

    status = (attendance.get("status") or "").strip()
    has_in = bool(attendance.get("in_time"))
    has_out = bool(attendance.get("out_time"))
    both = has_in and has_out
    any_punch = has_in or has_out
    excused = status in NON_PUNCH_STATUSES

    if excused:
        result["punch_case"] = "Punches not required by recorded status"
        if any_punch and status in ("On Leave", "Time Off", "Business Trip"):
            result["status_punch_conflict"] = 1
            result["review_notes"].append("Recorded leave/status overlaps observed punches")
    elif calendar_holiday or weekly_off:
        result["punch_case"] = "Recorded status on calendar day off"
        if status == "Absent" or (status == "Present" and not any_punch):
            result["status_punch_conflict"] = 1
            result["review_notes"].append("Attendance status contradicts day-off calendar")
    elif status == "Absent":
        result["punch_case"] = "Explicit absent status"
        if any_punch:
            result["status_punch_conflict"] = 1
            result["review_notes"].append("Absent status despite an observed punch")
    elif status in ("Present", "Half Day"):
        if both:
            result["punch_case"] = "Both punches present"
        elif any_punch:
            result["punch_case"] = "One-sided punch"
            result["missing_in"] = int(not has_in)
            result["missing_out"] = int(not has_out)
            result["review_notes"].append("One expected punch is missing")
        elif status == "Half Day":
            result["punch_case"] = "Half-day work not evidenced"
            result["unverified_half_day"] = 1
            result["review_notes"].append("Verify remaining work portion for half-day leave")
        else:
            result["punch_case"] = "Present without device punches"
            result["no_punch_day"] = 1
            result["review_notes"].append("Present marked with no device punches")
    else:
        result["punch_case"] = "Other attendance status"

    if both:
        first, last = _as_datetime(attendance.get("in_time")), _as_datetime(attendance.get("out_time"))
        if not first or not last:
            result["working_hours_issue"] = 1
            result["review_notes"].append("Cannot parse recorded punch times")
        else:
            try:
                span_hours = (last - first).total_seconds() / 3600
            except TypeError:
                span_hours = 0
            if not 0 < span_hours <= 24:
                result["working_hours_issue"] = 1
                result["review_notes"].append("Invalid or implausible clock-in/out sequence")
            else:
                result["gross_presence_hours"] = round(span_hours, 3)
                # Punch span is NOT payable or approved working time.
                raw_hours = attendance.get("working_hours")
                try:
                    zero_hours = raw_hours is None or float(raw_hours) == 0
                except (TypeError, ValueError):
                    zero_hours = True
                if zero_hours and span_hours >= 0.1:
                    result["working_hours_issue"] = 1
                    result["review_notes"].append("Working Hours is zero despite two punches")

    raw_missing_in = attendance.get("in_status") == "Missing"
    raw_missing_out = attendance.get("out_status") == "Missing"
    if (raw_missing_in and has_in) or (raw_missing_out and has_out):
        result["source_flag_issue"] = 1
        result["review_notes"].append("Stored Missing flag conflicts with a recorded punch")
    elif (excused or calendar_holiday or weekly_off) and (raw_missing_in or raw_missing_out):
        result["source_flag_issue"] = 1
        result["review_notes"].append("Stored Missing flag on an excused day is not a missing punch")

    result["needs_review"] = int(bool(
        result["missing_in"] or result["missing_out"] or result["no_punch_day"]
        or result["unverified_half_day"] or result["unrecorded_review_day"]
        or result["working_hours_issue"] or result["source_flag_issue"]
        or result["status_punch_conflict"]
    ))
    return result
