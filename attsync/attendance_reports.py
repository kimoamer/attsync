"""Read-only attendance reports based on submitted HRMS/HRIS Attendance records.

No report re-runs attendance rules or mutates payroll/attendance documents.
Unrecorded dates are explicitly labelled 'No Record', never inferred as Absent.
"""

from collections import defaultdict
from datetime import timedelta

import frappe
from frappe import _
from frappe.utils import cint, flt, get_datetime, getdate, today

from attsync.attendance_review import review_attendance

MAX_DAYS = 366
MAX_EMPLOYEES = 1000
MAX_GAP_DAYS = 62
MAX_GAP_EMPLOYEES = 200

ATTENDANCE_FIELDS = (
    "name", "employee", "attendance_date", "status", "shift", "in_time", "out_time",
    "working_hours", "late_entry", "early_exit", "leave_type", "leave_application",
    "in_status", "out_status", "late_entry_in_minutes", "early_exit_in_minutes",
    "overtime_in_minutes", "before_shift_in_minutes", "after_shift_in_minutes",
    "is_holiday", "exception",
)
EMPLOYEE_FIELDS = (
    "name", "employee_name", "attendance_device_id", "department", "company",
    "date_of_joining", "relieving_date", "holiday_list", "default_shift",
)


def _field_names(doctype, names):
    meta = frappe.get_meta(doctype)
    return [name for name in names if name == "name" or meta.has_field(name)]


def _date_range(filters):
    filters = frappe._dict(filters or {})
    if not filters.get("company") or not filters.get("from_date") or not filters.get("to_date"):
        frappe.throw(_("Company, From Date and To Date are required"))
    start, end = getdate(filters.from_date), getdate(filters.to_date)
    days = (end - start).days + 1
    if days <= 0 or days > MAX_DAYS:
        frappe.throw(_("Choose a valid date range of at most 366 days"))
    if (cint(filters.get("include_unrecorded")) or cint(filters.get("audit_gaps"))) and days > MAX_GAP_DAYS:
        frappe.throw(_("To include days with no attendance records, use a range of at most 62 days"))
    if not frappe.has_permission("Attendance", "read") or not frappe.has_permission("Employee", "read"):
        frappe.throw(_("You do not have permission to view employee attendance"), frappe.PermissionError)
    return filters, start, end


def _employees(filters):
    match = {"company": filters.company}
    if filters.get("department"):
        match["department"] = filters.department
    if filters.get("employee"):
        match["name"] = filters.employee
    rows = frappe.get_list(
        "Employee", filters=match, fields=_field_names("Employee", EMPLOYEE_FIELDS),
        order_by="employee_name asc, name asc", limit_page_length=0,
    )
    if len(rows) > MAX_EMPLOYEES:
        frappe.throw(_("More than 1000 employees matched. Select a department or employee."))
    if (cint(filters.get("include_unrecorded")) or cint(filters.get("audit_gaps"))) and len(rows) > MAX_GAP_EMPLOYEES:
        frappe.throw(_("To include unrecorded days, select at most 200 employees."))
    return rows


def _time_string(value):
    if value in (None, ""):
        return ""
    if isinstance(value, timedelta):
        minutes = int(value.total_seconds() // 60) % 1440
        return f"{minutes // 60:02d}:{minutes % 60:02d}"
    if hasattr(value, "strftime"):
        return value.strftime("%H:%M")
    return str(value).split()[-1][:5]


def _punch_string(value):
    return get_datetime(value).strftime("%H:%M") if value else ""


def _shift_data(names):
    if not names:
        return {}, {}
    fields = _field_names("Shift Type", (
        "name", "start_time", "end_time", "holiday_list", "enable_ramadan_timing",
        "ramadan_from_date", "ramadan_to_date", "ramadan_end_time",
    ))
    shifts = {
        row.name: row for row in frappe.get_all(
            "Shift Type", filters={"name": ["in", sorted(names)]}, fields=fields,
            limit_page_length=0,
        )
    }
    exceptional = {}
    if frappe.get_meta("Shift Type").has_field("exceptional_days") and frappe.db.exists(
        "DocType", "Shift Week Table"
    ):
        for row in frappe.get_all(
            "Shift Week Table", filters={"parent": ["in", sorted(names)], "parenttype": "Shift Type"},
            fields=["parent", "day", "start_time", "end_time"], limit_page_length=0,
        ):
            exceptional[(row.parent, row.day)] = row
    return shifts, exceptional


def _planned_shift(shift_name, day, shifts, exceptional):
    shift = shifts.get(shift_name)
    if not shift:
        return "", ""
    override = exceptional.get((shift_name, day.strftime("%A")))
    start = override.start_time if override else shift.get("start_time")
    end = override.end_time if override else shift.get("end_time")
    if (
        not override and cint(shift.get("enable_ramadan_timing"))
        and shift.get("ramadan_from_date") and shift.get("ramadan_to_date")
        and getdate(shift.ramadan_from_date) <= day <= getdate(shift.ramadan_to_date)
        and shift.get("ramadan_end_time")
    ):
        end = shift.ramadan_end_time
    return _time_string(start), _time_string(end)


def _holiday_days(company, employees, shifts, start, end):
    default_list = frappe.db.get_value("Company", company, "default_holiday_list")
    lists = {default_list} if default_list else set()
    lists.update(employee.get("holiday_list") for employee in employees if employee.get("holiday_list"))
    lists.update(shift.get("holiday_list") for shift in shifts.values() if shift.get("holiday_list"))
    if not lists:
        return {}, default_list
    dates = defaultdict(dict)
    for holiday in frappe.get_all(
        "Holiday", filters={"parent": ["in", sorted(lists)], "holiday_date": ["between", [start, end]]},
        fields=["parent", "holiday_date", "weekly_off"], limit_page_length=0,
    ):
        key = getdate(holiday.holiday_date)
        dates[holiday.parent][key] = bool(holiday.weekly_off)
    return dates, default_list


def _leave_info(attendances):
    leaves = {}
    refs = {x.get("leave_application") for x in attendances if x.get("leave_application")}
    if refs and frappe.has_permission("Leave Application", "read"):
        fields = _field_names("Leave Application", ("name", "half_day", "leave_day_fraction"))
        for leave in frappe.get_list(
            "Leave Application", filters={"name": ["in", sorted(refs)], "docstatus": 1},
            fields=fields, limit_page_length=0,
        ):
            leaves[leave.name] = leave
    leave_types = {}
    if frappe.db.exists("DocType", "Leave Type"):
        fields = _field_names("Leave Type", ("name", "is_lwp"))
        leave_types = {x.name: bool(x.get("is_lwp")) for x in frappe.get_all("Leave Type", fields=fields)}
    return leaves, leave_types


def _leave_fraction(attendance, leaves):
    fraction = 0.5 if attendance.get("status") == "Half Day" else 1.0
    leave = leaves.get(attendance.get("leave_application"))
    if not leave:
        return fraction
    fraction_name = (leave.get("leave_day_fraction") or "").lower()
    if "three quarter" in fraction_name:
        return 0.75
    if "quarter" in fraction_name:
        return 0.25
    if "half" in fraction_name or cint(leave.get("half_day")):
        return 0.5
    return fraction


def get_rows(filters):
    """Return (daily rows, visible employees); permission-aware primary queries."""
    filters, start, end = _date_range(filters)
    employees = _employees(filters)
    if not employees:
        return [], []
    employee_by_id = {x.name: x for x in employees}
    records = frappe.get_list(
        "Attendance",
        filters={
            "employee": ["in", list(employee_by_id)],
            "attendance_date": ["between", [start, end]],
            "docstatus": 1,
        },
        fields=_field_names("Attendance", ATTENDANCE_FIELDS),
        order_by="attendance_date asc, modified desc", limit_page_length=0,
    )
    by_date = {}
    days_by_employee = defaultdict(set)
    for att in records:
        day = getdate(att.attendance_date)
        by_date.setdefault((att.employee, day), att)
        days_by_employee[att.employee].add(day)
    names = {a.get("shift") for a in records if a.get("shift")}
    names.update(x.get("default_shift") for x in employees if x.get("default_shift"))
    shifts, exceptional = _shift_data(names)
    holidays, default_holiday_list = _holiday_days(filters.company, employees, shifts, start, end)
    leaves, leave_types = _leave_info(records)

    include_unrecorded = cint(filters.get("include_unrecorded"))
    # Exception analysis must include date gaps; never infer an Absent document.
    if filters.get("audit_gaps"):
        include_unrecorded = 1
    current_date = getdate(today())
    result = []
    for emp in employees:
        if include_unrecorded:
            count = (min(end, current_date) - start).days + 1
            employee_dates = sorted(
                {start + timedelta(days=i) for i in range(max(0, count))}
                | days_by_employee[emp.name]
            )
        else:
            employee_dates = sorted(days_by_employee[emp.name])
        for day in employee_dates:
            if emp.get("date_of_joining") and day < getdate(emp.date_of_joining):
                continue
            if emp.get("relieving_date") and day > getdate(emp.relieving_date):
                continue
            att = by_date.get((emp.name, day))
            shift_name = (att.get("shift") if att else None) or emp.get("default_shift")
            start_at, end_at = _planned_shift(shift_name, day, shifts, exceptional)
            holiday_list = (
                shifts[shift_name].get("holiday_list") if shift_name in shifts else None
            ) or emp.get("holiday_list") or default_holiday_list
            is_listed_holiday = day in holidays.get(holiday_list, {})
            is_weekend = holidays.get(holiday_list, {}).get(day, False)
            status = att.get("status") if att else "No Record"
            is_working = status in ("Present", "Work From Home", "Half Day")
            missing_in = bool(att and is_working and (
                att.get("in_status") == "Missing" or not att.get("in_time")
            ))
            missing_out = bool(att and is_working and (
                att.get("out_status") == "Missing" or not att.get("out_time")
            ))
            late = flt(att.get("late_entry_in_minutes")) if att else 0
            early = flt(att.get("early_exit_in_minutes")) if att else 0
            overtime = flt(att.get("overtime_in_minutes")) if att else 0
            if att and not overtime:
                overtime = flt(att.get("before_shift_in_minutes")) + flt(att.get("after_shift_in_minutes"))
            is_holiday = bool((att and cint(att.get("is_holiday"))) or is_listed_holiday)
            quality = review_attendance(
                att,
                calendar_holiday=is_holiday,
                weekly_off=is_weekend,
                current_day=(day >= current_date),
            )
            notes = []
            if status == "Absent":
                notes.append(_("Absent"))
            if quality["missing_in"]:
                notes.append(_("Missing clock-in"))
            if quality["missing_out"]:
                notes.append(_("Missing clock-out"))
            if late > 0 or (att and cint(att.get("late_entry"))):
                notes.append(_("Late arrival"))
            if early > 0 or (att and cint(att.get("early_exit"))):
                notes.append(_("Early departure"))
            exception = att.get("exception") if att else ""
            if exception:
                notes.append(str(exception))
            notes.extend(quality["review_notes"])
            result.append({
                "employee": emp.name,
                "employee_name": emp.get("employee_name") or emp.name,
                "device_id": emp.get("attendance_device_id") or "",
                "department": emp.get("department") or "",
                "attendance_date": day,
                "day_name": _(day.strftime("%A")),
                "shift": shift_name or "",
                "on_duty": start_at,
                "off_duty": end_at,
                "clock_in": _punch_string(att.get("in_time")) if att else "",
                "clock_out": _punch_string(att.get("out_time")) if att else "",
                "working_hours": flt(att.get("working_hours")) if att else 0,
                "gross_presence_hours": quality["gross_presence_hours"],
                "punch_case": quality["punch_case"],
                "no_punch_day": quality["no_punch_day"],
                "unverified_half_day": quality["unverified_half_day"],
                "unrecorded_review_day": quality["unrecorded_review_day"],
                "working_hours_issue": quality["working_hours_issue"],
                "source_flag_issue": quality["source_flag_issue"],
                "status_punch_conflict": quality["status_punch_conflict"],
                "needs_review": quality["needs_review"],
                "late_minutes": late,
                "early_minutes": early,
                "late_entry": int(bool(att and (cint(att.get("late_entry")) or late > 0))),
                "early_exit": int(bool(att and (cint(att.get("early_exit")) or early > 0))),
                "overtime_minutes": max(0, overtime),
                "missing_in": quality["missing_in"],
                "missing_out": quality["missing_out"],
                "absent": int(status == "Absent"),
                "weekend": int(is_weekend),
                "holiday": int(is_holiday),
                "status": status,
                "leave_type": att.get("leave_type") if att else "",
                "leave_fraction": _leave_fraction(att, leaves) if att else 0,
                "leave_is_lwp": bool(leave_types.get(att.get("leave_type"))) if att else False,
                "exception": " | ".join(notes),
                "attendance": att.name if att else "",
            })
    return result, employees


def _col(field, label, fieldtype="Data", width=110, options=None):
    result = {"fieldname": field, "label": _(label), "fieldtype": fieldtype, "width": width}
    if options:
        result["options"] = options
    return result


def detailed_columns():
    return [
        _col("employee", "الموظف", "Link", 120, "Employee"),
        _col("device_id", "رقم البصمة", width=100),
        _col("employee_name", "اسم الموظف", width=190),
        _col("department", "القسم", "Link", 150, "Department"),
        _col("attendance_date", "التاريخ", "Date", 105),
        _col("day_name", "اليوم", width=95),
        _col("shift", "الوردية", "Link", 135, "Shift Type"),
        _col("on_duty", "بداية الوردية", width=95),
        _col("off_duty", "نهاية الوردية", width=95),
        _col("clock_in", "الحضور", width=85),
        _col("clock_out", "الانصراف", width=85),
        _col("status", "الحالة", width=110),
        _col("working_hours", "ساعات العمل المسجلة", "Float", 145),
        _col("gross_presence_hours", "المدة بين البصمتين (ليست ساعات مستحقة)", "Float", 220),
        _col("punch_case", "تصنيف البصمات", width=235),
        _col("no_punch_day", "حضور بدون بصمات", "Check", 145),
        _col("unverified_half_day", "نصف يوم يحتاج مراجعة", "Check", 175),
        _col("unrecorded_review_day", "بدون سجل - للمراجعة", "Check", 165),
        _col("working_hours_issue", "مشكلة ساعات العمل", "Check", 145),
        _col("source_flag_issue", "تعارض علامات البصمة", "Check", 150),
        _col("status_punch_conflict", "تعارض الحالة مع البصمات", "Check", 175),
        _col("needs_review", "يحتاج مراجعة", "Check", 120),
        _col("late_minutes", "دقائق التأخير", "Float", 105),
        _col("early_minutes", "دقائق الانصراف المبكر", "Float", 145),
        _col("overtime_minutes", "دقائق الإضافي", "Float", 115),
        _col("missing_in", "غياب بصمة دخول", "Check", 125),
        _col("missing_out", "غياب بصمة خروج", "Check", 125),
        _col("absent", "غياب", "Check", 75),
        _col("weekend", "راحة أسبوعية", "Check", 115),
        _col("holiday", "عطلة", "Check", 75),
        _col("leave_type", "نوع الإجازة", width=140),
        _col("exception", "الاستثناء", width=230),
        _col("attendance", "سجل الحضور", "Link", 140, "Attendance"),
    ]


def filter_by_view(rows, view):
    if view in ("Clock In/Out Exceptions", "Clock"):
        return [r for r in rows if r["missing_in"] or r["missing_out"] or r["no_punch_day"] or r["unverified_half_day"] or r["source_flag_issue"]]
    if view in ("Shift Exceptions", "Shift"):
        return [r for r in rows if r["late_entry"] or r["early_exit"] or r["status_punch_conflict"]]
    if view in ("Misc Exceptions", "Other"):
        return [r for r in rows if r["unrecorded_review_day"] or r["working_hours_issue"] or r["absent"] or r["status"] in (
            "On Leave", "Half Day", "Business Trip", "Time Off", "No Record"
        ) or (r["exception"] and not (
            r["missing_in"] or r["missing_out"] or r["late_entry"] or r["early_exit"]
        ))]
    if view in ("All Exceptions", "All"):
        return [r for r in rows if r["needs_review"] or r["exception"] or r["status"] in (
            "On Leave", "Half Day", "Business Trip", "Time Off", "No Record"
        )]
    return rows


def _leave_bucket(row):
    if row["status"] not in ("On Leave", "Half Day") or not row["leave_type"]:
        return None
    leave_name = row["leave_type"].lower()
    fraction = row["leave_fraction"]
    if row["leave_is_lwp"] or "بالخصم" in leave_name or "without pay" in leave_name:
        if fraction == 0.25:
            return "unpaid_quarter_day"
        if fraction == 0.5:
            return "unpaid_half_day"
        if fraction == 0.75:
            return "unpaid_three_quarter_day"
        return "unpaid_leave"
    if "sick" in leave_name or "مرض" in leave_name:
        return "sick_leave"
    if "maternity" in leave_name or "ولاد" in leave_name or "وضع" in leave_name:
        return "maternity_leave"
    return "other_leave"


def summarize(rows, employees):
    per_employee = {
        e.name: {
            "employee": e.name,
            "device_id": e.get("attendance_device_id") or "",
            "employee_name": e.get("employee_name") or e.name,
            "department": e.get("department") or "",
            "late": 0, "early": 0, "absent": 0, "business_trip": 0,
            "sick_leave": 0.0, "unpaid_leave": 0.0,
            "unpaid_half_day": 0.0, "unpaid_quarter_day": 0.0,
            "maternity_leave": 0.0, "unpaid_three_quarter_day": 0.0,
            "other_leave": 0.0, "missing_punches": 0,
            "late_minutes": 0.0, "early_minutes": 0.0,
            "working_hours": 0.0, "overtime_minutes": 0.0,
            "unrecorded_days": 0, "holidays": 0,
            "gross_presence_hours": 0.0, "review_cases": 0,
            "no_punch_days": 0, "half_day_reviews": 0,
            "working_hours_issues": 0, "source_flag_issues": 0,
            "status_punch_conflicts": 0,
        }
        for e in employees
    }
    for r in rows:
        item = per_employee[r["employee"]]
        item["late"] += r["late_entry"]
        item["early"] += r["early_exit"]
        item["absent"] += r["absent"]
        item["missing_punches"] += r["missing_in"] + r["missing_out"]
        item["late_minutes"] += r["late_minutes"]
        item["early_minutes"] += r["early_minutes"]
        item["working_hours"] += r["working_hours"]
        item["gross_presence_hours"] += r["gross_presence_hours"]
        item["review_cases"] += r["needs_review"]
        item["no_punch_days"] += r["no_punch_day"]
        item["half_day_reviews"] += r["unverified_half_day"]
        item["working_hours_issues"] += r["working_hours_issue"]
        item["source_flag_issues"] += r["source_flag_issue"]
        item["status_punch_conflicts"] += r["status_punch_conflict"]
        item["overtime_minutes"] += r["overtime_minutes"]
        if r["status"] == "No Record":
            item["unrecorded_days"] += 1
        if r["holiday"]:
            item["holidays"] += 1
        if r["status"] == "Business Trip":
            item["business_trip"] += 1
        bucket = _leave_bucket(r)
        if bucket:
            item[bucket] += r["leave_fraction"]
    return list(per_employee.values())


def calculated_columns():
    # The first twelve data columns follow the user's September 2026 XLS template.
    return [
        _col("device_id", "AC-No", width=100),
        _col("employee_name", "Name", width=200),
        _col("late", "Late", "Int", 70),
        _col("early", "Early", "Int", 70),
        _col("absent", "Absent", "Int", 85),
        _col("business_trip", "مأمورية", "Int", 90),
        _col("sick_leave", "اجازة مرضى", "Float", 100),
        _col("unpaid_leave", "أجازة بالخصم", "Float", 120),
        _col("unpaid_half_day", "نصف يوم بالخصم", "Float", 135),
        _col("unpaid_quarter_day", "ربع يوم بالخصم", "Float", 130),
        _col("maternity_leave", "أجازة وضع (ولادة)", "Float", 140),
        _col("unpaid_three_quarter_day", "أجازة بالخصم 3/4 يوم", "Float", 160),
        _col("employee", "الموظف", "Link", 125, "Employee"),
        _col("department", "القسم", "Link", 150, "Department"),
        _col("other_leave", "إجازات أخرى", "Float", 110),
        _col("missing_punches", "بصمات ناقصة", "Int", 110),
        _col("late_minutes", "دقائق التأخير", "Float", 110),
        _col("early_minutes", "دقائق الانصراف المبكر", "Float", 155),
        _col("working_hours", "ساعات العمل المسجلة", "Float", 150),
        _col("gross_presence_hours", "مدة التواجد بين البصمتين", "Float", 190),
        _col("review_cases", "أيام تحتاج مراجعة", "Int", 150),
        _col("no_punch_days", "حضور بدون بصمات", "Int", 145),
        _col("half_day_reviews", "نصف يوم بدون بصمات", "Int", 150),
        _col("working_hours_issues", "خلل ساعات العمل", "Int", 135),
        _col("source_flag_issues", "تعارض علامات البصمات", "Int", 165),
        _col("status_punch_conflicts", "تعارض الحالة والبصمة", "Int", 170),
        _col("overtime_minutes", "دقائق الإضافي", "Float", 120),
        _col("holidays", "العطلات المسجلة", "Int", 125),
        _col("unrecorded_days", "أيام بدون سجل (عند تفعيل الخيار)", "Int", 215),
    ]
