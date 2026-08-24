import frappe
from erpnext.setup.doctype.employee.employee import get_holiday_list_for_employee
from erpnext.setup.doctype.holiday_list.holiday_list import is_holiday as is_holiday_in_list
from frappe.utils import get_datetime, get_time, getdate, now_datetime

REQUEST_DOCTYPE = "Attendance Sync Request"
REQUEST_EMPLOYEE_DOCTYPE = "Attendance Sync Employee"
REQUEST_BATCH_DOCTYPE = "Attendance Sync Batch"


@frappe.whitelist(methods=["GET"])
def get_sync_attendance_employees() -> dict:
    sync_request = _claim_next_sync_request()
    if not sync_request:
        frappe.response.update({"employeeNumbers": [], "dateRanges": []})
        return None

    employee_rows = _get_request_employee_rows(sync_request.name)
    employee_numbers = [row.attendance_device_id for row in employee_rows if row.attendance_device_id]

    if not employee_numbers:
        frappe.db.set_value(
            REQUEST_DOCTYPE,
            sync_request.name,
            {"status": "Failed", "last_error": "No selected employees with attendance device IDs."},
            update_modified=False,
        )
        frappe.local.flags.commit = True
        frappe.response.update({"employeeNumbers": [], "dateRanges": []})
        return None

    from_date = str(getdate(sync_request.from_date))
    to_date = str(getdate(sync_request.to_date))

    frappe.response.update(
        {
            "requestId": sync_request.name,
            "company": sync_request.company,
            "employeeNumbers": employee_numbers,
            "fromDate": from_date,
            "toDate": to_date,
            "dateRanges": [from_date, to_date],
        }
    )
    return None


@frappe.whitelist(methods=["POST"])
def sync_attendance_records(data: dict | str | None = None) -> dict:
    payload = _get_payload(data)
    request_id = payload.get("requestId") or payload.get("request_id")

    if payload.get("finalize"):
        return _finalize_sync_request(payload, request_id)

    records = payload.get("records") or []
    if not records:
        return {"batchId": payload.get("batchId"), "results": []}

    if not request_id:
        open_requests = frappe.get_all(
            REQUEST_DOCTYPE,
            filters={"status": ["in", ["Ready", "In Progress"]]},
            fields=["name", "status"],
            order_by="creation asc",
            limit_page_length=1,
        )
        if open_requests:
            open_request = open_requests[0]
            frappe.throw(
                f"Attendance upload is missing requestId while Attendance Sync Request "
                f"{open_request.name} is {open_request.status}. Use a request-aware sync client "
                "or complete/reset the open request before using legacy sync."
            )

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

    _validate_request_for_upload(request_id)
    batch_id = payload.get("batchId")
    if not batch_id:
        frappe.throw("batchId is required when requestId is supplied")

    existing_batch = frappe.db.get_value(
        REQUEST_BATCH_DOCTYPE,
        {"parent": request_id, "parenttype": REQUEST_DOCTYPE, "batch_id": batch_id},
        ["name", "status", "job_id"],
        as_dict=True,
    )
    if existing_batch:
        return {
            "batchId": batch_id,
            "queued": existing_batch.status in {"Queued", "Processing"},
            "status": existing_batch.status,
            "jobId": existing_batch.job_id,
            "duplicate": True,
        }

    request_doc = frappe.get_doc(REQUEST_DOCTYPE, request_id)
    request_doc.status = "In Progress"
    request_doc.last_sync_attempt = now_datetime()
    batch_row = request_doc.append(
        "batches",
        {
            "batch_id": batch_id,
            "status": "Queued",
            "records_received": len(records),
        },
    )
    request_doc.flags.ignore_permissions = True
    request_doc.save()

    job = frappe.enqueue(
        _process_attendance_records,
        queue="long",
        timeout=6000,
        payload=payload,
        enqueue_after_commit=True,
    )
    job_id = getattr(job, "id", None) or getattr(job, "get_id", lambda: None)()
    if job_id:
        frappe.db.set_value(REQUEST_BATCH_DOCTYPE, batch_row.name, "job_id", job_id, update_modified=False)

    return {"batchId": batch_id, "queued": True, "jobId": job_id}


def _claim_next_sync_request():
    rows = frappe.db.sql(
        f"""
        select name
        from `tab{REQUEST_DOCTYPE}`
        where status = 'Ready'
        order by creation asc
        limit 1
        for update
        """,
        as_dict=True,
    )
    if not rows:
        return None

    request_doc = frappe.get_doc(REQUEST_DOCTYPE, rows[0].name)
    request_doc.status = "In Progress"
    request_doc.claimed_at = now_datetime()
    request_doc.last_sync_attempt = now_datetime()
    request_doc.last_error = None
    request_doc.flags.ignore_permissions = True
    request_doc.save()

    frappe.local.flags.commit = True
    return request_doc


def _get_request_employee_rows(request_id: str) -> list:
    return frappe.get_all(
        REQUEST_EMPLOYEE_DOCTYPE,
        filters={
            "parent": request_id,
            "parenttype": REQUEST_DOCTYPE,
            "parentfield": "employees",
            "include_in_sync": 1,
        },
        fields=["name", "employee", "employee_name", "attendance_device_id"],
        order_by="idx asc",
    )


def _validate_request_for_upload(request_id: str) -> None:
    if not frappe.db.exists(REQUEST_DOCTYPE, request_id):
        frappe.throw(f"Attendance Sync Request {request_id} does not exist")

    status = frappe.db.get_value(REQUEST_DOCTYPE, request_id, "status")
    if status not in {"Ready", "In Progress"}:
        frappe.throw(f"Attendance Sync Request {request_id} is not open for upload (status: {status})")


def _finalize_sync_request(payload: dict, request_id: str | None) -> dict:
    if not request_id:
        return {"finalized": False, "reason": "requestId is required"}

    if not frappe.db.exists(REQUEST_DOCTYPE, request_id):
        frappe.throw(f"Attendance Sync Request {request_id} does not exist")

    request_doc = frappe.get_doc(REQUEST_DOCTYPE, request_id)

    if payload.get("dryRun"):
        if request_doc.batches:
            frappe.throw("Cannot reset a dry-run request after attendance batches have been queued")
        request_doc.status = "Ready"
        request_doc.claimed_at = None
        request_doc.last_sync_attempt = now_datetime()
        request_doc.finalize_received = 0
        request_doc.expected_batches = 0
        request_doc.last_error = None
        request_doc.flags.ignore_permissions = True
        request_doc.save()
        return {"requestId": request_id, "finalized": True, "status": request_doc.status, "dryRun": True}

    request_doc.finalize_received = 1
    request_doc.expected_batches = int(payload.get("expectedBatches") or 0)
    request_doc.last_sync_attempt = now_datetime()

    failed_numbers = {
        str(value)
        for value in (payload.get("failedEmployees") or [])
        if value not in (None, "")
    }
    for employee_number in failed_numbers:
        _mark_request_employee_failed(
            request_id,
            employee_number,
            "Attendance query or upload failed on the sync client.",
        )

    request_doc.flags.ignore_permissions = True
    request_doc.save()
    status = _refresh_request_status(request_id)
    return {"requestId": request_id, "finalized": True, "status": status}


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


def _create_or_update_attendance(record: dict, request_id: str | None = None) -> dict:
    employee_number = record.get("employeeNo")
    attendance_date = record.get("workDate")
    record_id = record.get("recordId")
    if not employee_number or not attendance_date:
        return {
            "status": "skipped",
            "reason": "Missing employeeNo or workDate",
            "recordId": record_id,
        }

    employee = _get_employee_by_number(employee_number)
    if not employee:
        return {
            "status": "skipped",
            "reason": "Employee not found",
            "employeeNo": employee_number,
            "recordId": record_id,
        }

    existing_attendance = frappe.db.exists(
        "Attendance",
        {"employee": employee, "attendance_date": attendance_date},
    )

    shift_type = _get_shift_type(record)
    clock_in = record.get("clockIn")
    clock_out = record.get("clockOut")
    exception = record.get("exception")

    if not existing_attendance:
        if (
            _is_holiday_for_employee(employee, attendance_date, shift_type)
            and not clock_in
            and not clock_out
            and not exception
        ):
            return {
                "status": "skipped",
                "reason": "Holiday with no check-in/out",
                "employee": employee,
                "recordId": record_id,
            }

        attendance = frappe.new_doc("Attendance")
        attendance.employee = employee
        attendance.attendance_date = attendance_date
        status = "created"
    else:
        attendance = frappe.get_doc("Attendance", existing_attendance)
        existing_status = (attendance.status or "").strip()
        existing_record_id = None
        if attendance.meta.has_field("atsync_record_id"):
            existing_record_id = attendance.get("atsync_record_id")

        owned_by_attsync = bool(record_id and existing_record_id and existing_record_id == record_id)
        if existing_status != "Absent" and not owned_by_attsync:
            return {
                "status": "skipped",
                "reason": f"Existing attendance status is protected: {existing_status or 'Unknown'}",
                "attendance": attendance.name,
                "employee": employee,
                "existingStatus": existing_status,
                "recordId": record_id,
            }

        status = "updated"

    base_status = _derive_status(record, employee, shift_type)

    _set_if_field(attendance, "status", base_status)
    _set_if_field(attendance, "shift", shift_type)
    _set_if_field(attendance, "in_time", _get_datetime_value(clock_in))
    _set_if_field(attendance, "out_time", _get_datetime_value(clock_out))
    _set_if_field(attendance, "late_entry_in_minutes", record.get("lateMinutes"))
    _set_if_field(attendance, "early_exit_in_minutes", record.get("earlyMinutes"))
    _set_if_field(attendance, "exception", exception)
    _set_if_field(attendance, "overtime_in_minutes", _get_overtime_minutes(record))
    _set_if_field(attendance, "atsync_record_id", record_id)

    integration_context = frappe._dict(
        {
            "employee": employee,
            "attendance_date": attendance_date,
            "shift_type": shift_type,
            "base_status": base_status,
            "late_entry": bool(record.get("lateMinutes")),
            "early_exit": bool(record.get("earlyMinutes")),
            "late_entry_in_minutes": record.get("lateMinutes") or 0,
            "early_exit_in_minutes": record.get("earlyMinutes") or 0,
            "request_id": request_id,
            "record": record,
        }
    )

    attendance.flags.ignore_permissions = True

    if status == "created":
        _run_attendance_integrations(attendance, integration_context, "before_insert")
        attendance.insert()
        attendance.submit()
        _run_attendance_integrations(attendance, integration_context, "after_submit")
    else:
        if attendance.docstatus == 1:
            attendance.flags.ignore_validate_update_after_submit = True
        attendance.save()

    return {
        "status": status,
        "attendance": attendance.name,
        "employee": employee,
        "employeeNo": employee_number,
        "recordId": record_id,
    }


def _run_attendance_integrations(attendance, context, event):
    methods = frappe.get_hooks("attsync_attendance_integration") or []
    for method in methods:
        frappe.get_attr(method)(attendance=attendance, context=context, event=event)


def _get_employee_by_number(employee_number: str) -> str | None:
    employee = frappe.db.get_value("Employee", {"attendance_device_id": employee_number}, "name")
    if employee:
        return employee
    return frappe.db.get_value("Employee", {"name": employee_number}, "name")


def _process_attendance_records(payload: dict) -> dict:
    records = payload.get("records") or []
    request_id = payload.get("requestId") or payload.get("request_id")
    batch_id = payload.get("batchId")

    if request_id and batch_id:
        _set_batch_status(request_id, batch_id, "Processing")
        frappe.db.commit()

    results = []
    try:
        for record in records:
            results.append(_create_or_update_attendance(record, request_id=request_id))

        if request_id and batch_id:
            _record_batch_success(request_id, batch_id, records, results)
            _refresh_request_status(request_id)

        return {"batchId": batch_id, "results": results}
    except Exception as exc:
        frappe.db.rollback()
        if request_id and batch_id:
            failed_employee_numbers = [record.get("employeeNo") for record in records if record.get("employeeNo")]
            _record_batch_failure(request_id, batch_id, failed_employee_numbers, str(exc))
            _refresh_request_status(request_id)
            frappe.db.commit()
        raise


def _set_batch_status(request_id: str, batch_id: str, status: str) -> None:
    batch_name = frappe.db.get_value(
        REQUEST_BATCH_DOCTYPE,
        {"parent": request_id, "parenttype": REQUEST_DOCTYPE, "batch_id": batch_id},
        "name",
    )
    if batch_name:
        frappe.db.set_value(REQUEST_BATCH_DOCTYPE, batch_name, "status", status, update_modified=False)


def _record_batch_success(request_id: str, batch_id: str, records: list, results: list) -> None:
    batch_name = frappe.db.get_value(
        REQUEST_BATCH_DOCTYPE,
        {"parent": request_id, "parenttype": REQUEST_DOCTYPE, "batch_id": batch_id},
        "name",
    )
    if not batch_name:
        return

    synced_count = 0
    failed_count = 0
    per_employee: dict[str, dict[str, int | str]] = {}

    for record, result in zip(records, results, strict=False):
        employee_number = str(record.get("employeeNo") or "")
        if not employee_number:
            continue

        bucket = per_employee.setdefault(employee_number, {"found": 0, "synced": 0, "failed": 0, "error": ""})
        bucket["found"] += 1
        if _is_successful_record_result(result):
            bucket["synced"] += 1
            synced_count += 1
        else:
            bucket["failed"] += 1
            failed_count += 1
            bucket["error"] = result.get("reason") or "Attendance record was not applied."

    for employee_number, counts in per_employee.items():
        row_name = _get_request_employee_row_name(request_id, employee_number)
        if not row_name:
            continue

        current = frappe.db.get_value(
            REQUEST_EMPLOYEE_DOCTYPE,
            row_name,
            ["records_found", "records_synced"],
            as_dict=True,
        )
        values = {
            "records_found": int((current.records_found if current else 0) or 0) + int(counts["found"]),
            "records_synced": int((current.records_synced if current else 0) or 0) + int(counts["synced"]),
        }
        if counts["failed"]:
            values.update({"failed": 1, "error_message": counts["error"]})
        frappe.db.set_value(REQUEST_EMPLOYEE_DOCTYPE, row_name, values, update_modified=False)

    frappe.db.set_value(
        REQUEST_BATCH_DOCTYPE,
        batch_name,
        {
            "status": "Completed" if failed_count == 0 else "Failed",
            "records_synced": synced_count,
            "records_failed": failed_count,
            "error_message": None if failed_count == 0 else "One or more attendance records were not applied.",
        },
        update_modified=False,
    )


def _record_batch_failure(request_id: str, batch_id: str, employee_numbers: list[str], error: str) -> None:
    batch_name = frappe.db.get_value(
        REQUEST_BATCH_DOCTYPE,
        {"parent": request_id, "parenttype": REQUEST_DOCTYPE, "batch_id": batch_id},
        "name",
    )
    if batch_name:
        frappe.db.set_value(
            REQUEST_BATCH_DOCTYPE,
            batch_name,
            {"status": "Failed", "error_message": error[:1000]},
            update_modified=False,
        )

    for employee_number in set(employee_numbers):
        _mark_request_employee_failed(request_id, employee_number, error)

    frappe.db.set_value(REQUEST_DOCTYPE, request_id, "last_error", error[:1000], update_modified=False)


def _is_successful_record_result(result: dict) -> bool:
    if result.get("status") in {"created", "updated"}:
        return True
    return result.get("status") == "skipped" and result.get("reason") == "Holiday with no check-in/out"


def _get_request_employee_row_name(request_id: str, employee_number: str) -> str | None:
    return frappe.db.get_value(
        REQUEST_EMPLOYEE_DOCTYPE,
        {
            "parent": request_id,
            "parenttype": REQUEST_DOCTYPE,
            "parentfield": "employees",
            "attendance_device_id": employee_number,
            "include_in_sync": 1,
        },
        "name",
    )


def _mark_request_employee_failed(request_id: str, employee_number: str, error: str) -> None:
    row_name = _get_request_employee_row_name(request_id, str(employee_number))
    if not row_name:
        return
    frappe.db.set_value(
        REQUEST_EMPLOYEE_DOCTYPE,
        row_name,
        {"failed": 1, "synced": 0, "error_message": (error or "Sync failed")[:1000]},
        update_modified=False,
    )


def _refresh_request_status(request_id: str) -> str:
    request_doc = frappe.get_doc(REQUEST_DOCTYPE, request_id)
    selected_rows = [row for row in request_doc.employees if row.include_in_sync]
    batch_rows = list(request_doc.batches or [])

    if not request_doc.finalize_received:
        status = "In Progress"
    else:
        expected_batches = int(request_doc.expected_batches or 0)
        active_batches = [row for row in batch_rows if row.status in {"Queued", "Processing"}]
        if active_batches or len(batch_rows) < expected_batches:
            status = "In Progress"
        else:
            now = now_datetime()
            failed_rows = [row for row in selected_rows if row.failed]
            for row in selected_rows:
                if row.failed:
                    if row.synced:
                        frappe.db.set_value(REQUEST_EMPLOYEE_DOCTYPE, row.name, "synced", 0, update_modified=False)
                    continue
                frappe.db.set_value(
                    REQUEST_EMPLOYEE_DOCTYPE,
                    row.name,
                    {"synced": 1, "last_synced_at": now, "error_message": None},
                    update_modified=False,
                )

            if failed_rows and len(failed_rows) == len(selected_rows):
                status = "Failed"
            elif failed_rows:
                status = "Partial"
            else:
                status = "Completed"

    frappe.db.set_value(
        REQUEST_DOCTYPE,
        request_id,
        {
            "status": status,
            "completed_at": now_datetime() if status in {"Completed", "Partial", "Failed"} else None,
        },
        update_modified=False,
    )
    _update_request_counts(request_id)
    return status


def _update_request_counts(request_id: str) -> None:
    rows = frappe.get_all(
        REQUEST_EMPLOYEE_DOCTYPE,
        filters={"parent": request_id, "parenttype": REQUEST_DOCTYPE, "parentfield": "employees", "include_in_sync": 1},
        fields=["synced", "failed"],
    )
    total = len(rows)
    synced = sum(1 for row in rows if row.synced)
    failed = sum(1 for row in rows if row.failed)
    frappe.db.set_value(
        REQUEST_DOCTYPE,
        request_id,
        {
            "total_employees": total,
            "synced_employees": synced,
            "failed_employees": failed,
            "pending_employees": max(0, total - synced - failed),
        },
        update_modified=False,
    )


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


def _derive_status(record: dict, employee: str | None, shift_type: str | None) -> str:
    exception = record.get("exception")
    if isinstance(exception, str) and "leave" in exception.lower():
        return "On Leave"

    attendance_date = record.get("workDate")
    clock_in = record.get("clockIn")
    clock_out = record.get("clockOut")
    if (
        employee
        and attendance_date
        and _is_holiday_for_employee(employee, attendance_date, shift_type)
        and not clock_in
        and not clock_out
        and not exception
    ):
        return _get_holiday_status()

    if not clock_in and not clock_out and not exception:
        return "Absent"
    return "Present"


def _is_holiday_for_employee(employee: str, attendance_date, shift_type: str | None) -> bool:
    date_value = getdate(attendance_date)
    holiday_lists = set()
    try:
        employee_list = get_holiday_list_for_employee(employee, raise_exception=False)
    except TypeError:
        employee_list = get_holiday_list_for_employee(employee, False)

    if employee_list:
        holiday_lists.add(employee_list)

    if shift_type and frappe.get_meta("Shift Type").has_field("holiday_list"):
        shift_list = frappe.db.get_value("Shift Type", shift_type, "holiday_list")
        if shift_list:
            holiday_lists.add(shift_list)

    return any(is_holiday_in_list(holiday_list, date_value) for holiday_list in holiday_lists)


def _get_holiday_status() -> str:
    status_field = frappe.get_meta("Attendance").get_field("status")
    if status_field and status_field.options:
        options = [option.strip() for option in status_field.options.split("\n") if option.strip()]
        if "Holiday" in options:
            return "Holiday"
    return "Present"
