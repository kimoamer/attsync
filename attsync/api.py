import frappe
from frappe.utils import get_datetime, get_time


@frappe.whitelist(methods=["GET"])
def get_sync_attendance_employees() -> dict:
    employee_rows = _get_payroll_entry_employees()
    if not employee_rows:
        return {"employeeNumbers": [], "dateRanges": []}

    selected_parent = employee_rows[0].parent
    filtered_rows = [row for row in employee_rows if row.parent == selected_parent]
    employee_numbers = _get_employee_numbers([row.employee for row in filtered_rows])

    _mark_payroll_entry_synced(selected_parent)

    return {
        "employeeNumbers": employee_numbers,
        "dateRanges": _get_attendance_date_range(selected_parent),
    }


@frappe.whitelist(methods=["POST"])
def sync_attendance_records(data: dict | str | None = None) -> dict:
    payload = _get_payload(data)
    records = payload.get("records") or []

    if not records:
        return {"batchId": payload.get("batchId"), "results": []}

    job = frappe.enqueue(
        _process_attendance_records,
        queue="long",
        timeout=6000,
        payload=payload,
    )

    return {
        "batchId": payload.get("batchId"),
        "queued": True,
        "jobId": getattr(job, "id", None) or getattr(job, "get_id", lambda: None)(),
    }


def _get_payroll_entry_employees() -> list:
    payroll_entries = frappe.db.get_all(
        "Payroll Entry",
        filters={"docstatus": 0, "sync_attendance": 1, "synced": 0},
        pluck="name",
        order_by="posting_date asc",
    )

    if not payroll_entries:
        return []

    return frappe.db.get_all(
        "Payroll Employee Detail",
        filters={"parent": ["in", payroll_entries]},
        fields=["employee", "parent"],
        order_by="parent asc, idx asc",
    )


def _get_payload(data: dict | str | None) -> dict:
    if data:
        return frappe.parse_json(data) if isinstance(data, str) else data

    if frappe.request:
        payload = frappe.request.get_json(silent=True)
        if payload:
            return payload

        raw = frappe.request.get_data()
        if raw:
            return frappe.parse_json(raw)

    return frappe.local.form_dict or {}


def _get_employee_numbers(employees: list[str]) -> list[str]:
    ordered_employees = _dedupe_preserve_order([emp for emp in employees if emp])
    if not ordered_employees:
        return []

    employee_rows = frappe.get_all(
        "Employee",
        filters={"name": ["in", ordered_employees]},
        fields=["name", "employee_number"],
    )
    employee_map = {row.name: (row.employee_number or row.name) for row in employee_rows}

    return [employee_map.get(employee, employee) for employee in ordered_employees]


def _get_attendance_date_range(payroll_entry: str) -> list:
    if not payroll_entry:
        return []

    date_fields = frappe.db.get_value(
        "Payroll Entry",
        payroll_entry,
        ["attendance_start_date", "attendance_end_date"],
        as_dict=True,
    )
    if not date_fields:
        return []

    start_date = date_fields.attendance_start_date or date_fields.start_date
    end_date = date_fields.attendance_end_date or date_fields.end_date
    if not (start_date and end_date):
        return []

    return [start_date, end_date]


def _create_or_update_attendance(record: dict) -> dict:
    employee_number = record.get("employeeNo")
    attendance_date = record.get("workDate")
    if not employee_number or not attendance_date:
        return {
            "status": "skipped",
            "reason": "Missing employeeNo or workDate",
            "recordId": record.get("recordId"),
        }

    employee = _get_employee_by_number(employee_number)
    if not employee:
        return {
            "status": "skipped",
            "reason": "Employee not found",
            "employeeNo": employee_number,
            "recordId": record.get("recordId"),
        }

    existing_attendance = frappe.db.exists(
        "Attendance",
        {"employee": employee, "attendance_date": attendance_date},
    )
    if existing_attendance:
        attendance = frappe.get_doc("Attendance", existing_attendance)
        status = "updated"
    else:
        attendance = frappe.new_doc("Attendance")
        attendance.employee = employee
        attendance.attendance_date = attendance_date
        status = "created"

    _set_if_field(attendance, "status",  _derive_status(record))
    _set_if_field(attendance, "shift", _get_shift_type(record))
    _set_if_field(attendance, "in_time", _get_datetime_value(record.get("clockIn")))
    _set_if_field(attendance, "out_time", _get_datetime_value(record.get("clockOut")))
    _set_if_field(attendance, "late_entry_in_minutes", record.get("lateMinutes"))
    _set_if_field(attendance, "early_exit_in_minutes", record.get("earlyMinutes"))
    _set_if_field(attendance, "exception", record.get("exception"))
    _set_if_field(attendance, "overtime_in_minutes", _get_overtime_minutes(record))

    attendance.flags.ignore_permissions = True
    if status == "created":
        attendance.insert()
    else:
        attendance.save()

    _mark_employee_synced(employee)

    return {
        "status": status,
        "attendance": attendance.name,
        "employee": employee,
        "recordId": record.get("recordId"),
    }


def _get_employee_by_number(employee_number: str) -> str | None:
    employee = frappe.db.get_value("Employee", {"employee_number": employee_number}, "name")
    if employee:
        return employee

    return frappe.db.get_value("Employee", {"name": employee_number}, "name")


def _process_attendance_records(payload: dict) -> dict:
    records = payload.get("records") or []
    results = []
    for record in records:
        results.append(_create_or_update_attendance(record))

    return {
        "batchId": payload.get("batchId"),
        "results": results,
    }


def _mark_employee_synced(employee: str) -> None:
    payroll_meta = frappe.get_meta("Payroll Entry")
    if not payroll_meta.has_field("synced"):
        return

    parent_rows = frappe.db.get_all(
        "Payroll Employee Detail",
        filters={"employee": employee},
        fields=["parent"],
    )
    parent_names = [row.parent for row in parent_rows if row.parent]
    if not parent_names:
        return

    filters = {"name": ["in", list({name for name in parent_names})]}
    if payroll_meta.has_field("sync_attendance"):
        filters["sync_attendance"] = 1
    filters["synced"] = 0

    eligible_parents = frappe.db.get_all(
        "Payroll Entry",
        filters=filters,
        pluck="name",
    )
    for payroll_entry in eligible_parents:
        _mark_payroll_entry_synced(payroll_entry)


def _mark_payroll_entry_synced(payroll_entry: str) -> None:
    if not payroll_entry:
        return
    if not frappe.get_meta("Payroll Entry").has_field("synced"):
        return
    frappe.db.set_value("Payroll Entry", payroll_entry, "synced", 1, update_modified=False)


def _set_if_field(doc, fieldname: str, value) -> None:
    if value in (None, ""):
        return
    if doc.meta.has_field(fieldname):
        doc.set(fieldname, value)


def _get_datetime_value(value):
    if not value:
        return None
    return get_datetime(value)


def _get_time_value(value):
    if not value:
        return None
    return get_time(value)


def _get_overtime_minutes(record: dict) -> int | None:
    return (
        record.get("overtimeMinutes")
        or record.get("overtime_minutes")
        or record.get("overtime_in_minutes")
        or record.get("overtimeInMinutes")
    )


def _get_shift_type(record: dict) -> str | None:
    shift_id = record.get("shiftId")
    if shift_id is not None:
        shift_name = frappe.db.get_value("Shift Type", {"shift_id": shift_id}, "name")
        if shift_name:
            return shift_name

    return record.get("shiftName")


def _derive_status(record: dict) -> str:
    exception = record.get("exception")
    if isinstance(exception, str) and "leave" in exception.lower():
        return "On Leave"

    clock_in = record.get("clockIn")
    clock_out = record.get("clockOut")
    if not clock_in and not clock_out and not exception:
        return "Absent"

    return "Present"


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen = set()
    output = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        output.append(value)
    return output
