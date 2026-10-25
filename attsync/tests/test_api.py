from unittest import TestCase
from unittest.mock import MagicMock, patch

from attsync.api import (
    _get_attendance_indicators,
    _is_successful_record_result,
    _set_if_field,
    _should_protect_existing_attendance,
    _create_or_update_attendance,
    _get_sync_request_status,
)


class _Meta:
    def has_field(self, fieldname):
        return fieldname == "in_status"


class _Document:
    meta = _Meta()

    def __init__(self):
        self.values = {"in_status": "Missing"}

    def set(self, fieldname, value):
        self.values[fieldname] = value


class TestExistingAttendanceProtection(TestCase):
    def test_on_leave_is_protected_even_when_owned_by_attsync(self):
        self.assertTrue(
            _should_protect_existing_attendance(
                "On Leave",
                owned_by_attsync=True,
            )
        )

    def test_leave_half_day_is_protected_even_when_owned_by_attsync(self):
        self.assertTrue(
            _should_protect_existing_attendance(
                "Half Day",
                owned_by_attsync=True,
                leave_application="HR-LAP-0001",
            )
        )

    def test_absent_can_still_be_updated(self):
        self.assertFalse(
            _should_protect_existing_attendance(
                "Absent",
                owned_by_attsync=False,
            )
        )

    def test_owned_present_record_can_still_be_refreshed(self):
        self.assertFalse(
            _should_protect_existing_attendance(
                "Present",
                owned_by_attsync=True,
            )
        )

    def test_unowned_present_record_remains_protected(self):
        self.assertTrue(
            _should_protect_existing_attendance(
                "Present",
                owned_by_attsync=False,
            )
        )


class TestRecordResultClassification(TestCase):
    def test_protected_attendance_is_a_successful_no_op(self):
        self.assertTrue(
            _is_successful_record_result(
                {
                    "status": "skipped",
                    "reason": "Existing attendance status is protected: On Leave",
                }
            )
        )

    def test_unexpected_skip_remains_a_failure(self):
        self.assertFalse(
            _is_successful_record_result(
                {
                    "status": "skipped",
                    "reason": "Employee not found",
                }
            )
        )


class TestAttendanceIndicators(TestCase):
    def test_allow_empty_clears_a_previous_missing_status(self):
        attendance = _Document()

        _set_if_field(attendance, "in_status", "", allow_empty=True)

        self.assertEqual(attendance.values["in_status"], "")

    def test_marks_both_missing_punches(self):
        indicators = _get_attendance_indicators({})

        self.assertEqual(indicators["in_status"], "Missing")
        self.assertEqual(indicators["out_status"], "Missing")
        self.assertFalse(indicators["late_entry"])
        self.assertFalse(indicators["early_exit"])
        self.assertEqual(indicators["late_entry_in_minutes"], 0)
        self.assertEqual(indicators["early_exit_in_minutes"], 0)

    def test_zero_string_minutes_do_not_set_flags(self):
        indicators = _get_attendance_indicators(
            {
                "clockIn": "2026-08-24 08:00:00",
                "clockOut": "2026-08-24 16:00:00",
                "lateMinutes": "0",
                "earlyMinutes": "0",
            }
        )

        self.assertEqual(indicators["in_status"], "")
        self.assertEqual(indicators["out_status"], "")
        self.assertFalse(indicators["late_entry"])
        self.assertFalse(indicators["early_exit"])

    def test_positive_minutes_set_flags_for_existing_punches(self):
        indicators = _get_attendance_indicators(
            {
                "clockIn": "2026-08-24 08:17:00",
                "clockOut": "2026-08-24 15:45:00",
                "lateMinutes": 17,
                "earlyMinutes": 15,
            }
        )

        self.assertTrue(indicators["late_entry"])
        self.assertTrue(indicators["early_exit"])
        self.assertEqual(indicators["late_entry_in_minutes"], 17)
        self.assertEqual(indicators["early_exit_in_minutes"], 15)

    def test_missing_punch_cannot_be_late_or_early(self):
        indicators = _get_attendance_indicators(
            {
                "clockIn": None,
                "clockOut": None,
                "lateMinutes": 20,
                "earlyMinutes": 30,
            }
        )

        self.assertFalse(indicators["late_entry"])
        self.assertFalse(indicators["early_exit"])


class TestCancelledAttendanceRecovery(TestCase):
    @patch("attsync.api._get_employee_by_number", return_value="EMP-001")
    @patch("attsync.api.frappe")
    @patch("attsync.api._is_holiday_for_employee", return_value=False)
    @patch("attsync.api._derive_status", return_value="Present")
    @patch("attsync.api._get_attendance_indicators", return_value={
        "in_status": "", "out_status": "", "late_entry": False,
        "early_exit": False, "late_entry_in_minutes": 0,
        "early_exit_in_minutes": 0,
    })
    @patch("attsync.api._get_shift_type", return_value=None)
    def test_lookup_excludes_cancelled_document(
        self, _shift, _indicators, _derive, _holiday, frappe_mock, _employee
    ):
        frappe_mock.db.get_value.return_value = None
        attendance = MagicMock()
        attendance.name = "ATT-NEW"
        attendance.meta.has_field.return_value = False
        frappe_mock.new_doc.return_value = attendance

        result = _create_or_update_attendance({
            "employeeNo": "1001", "workDate": "2026-10-25",
            "recordId": "record-1", "clockIn": "2026-10-25 08:00:00",
        })

        self.assertEqual(result["status"], "created")
        self.assertEqual(
            frappe_mock.db.get_value.call_args.args[1]["docstatus"],
            ["!=", 2],
        )
        attendance.insert.assert_called_once()
        attendance.submit.assert_called_once()


class TestRequestStatusReceipt(TestCase):
    @patch("attsync.api.frappe")
    def test_reports_server_processing_not_just_batch_acceptance(self, frappe_mock):
        frappe_mock.db.exists.return_value = True
        request = MagicMock()
        request.status = "In Progress"
        request.finalize_received = 0
        request.expected_batches = 2
        batch = MagicMock(status="Processing", records_synced=0, records_failed=0)
        request.batches = [batch]
        frappe_mock.get_doc.return_value = request

        result = _get_sync_request_status("ATT-SYNC-0001")

        self.assertEqual(result["status"], "In Progress")
        self.assertEqual(result["pendingBatches"], 1)
        self.assertEqual(result["receivedBatches"], 1)
        self.assertEqual(result["expectedBatches"], 2)
