from unittest import TestCase

from attsync.api import (
    _get_attendance_indicators,
    _is_successful_record_result,
    _set_if_field,
    _should_protect_existing_attendance,
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
