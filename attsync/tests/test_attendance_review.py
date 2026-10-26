"""Regression coverage for attendance quality flags (read-only, not payroll)."""

from datetime import datetime
from unittest import TestCase

from attsync.attendance_review import review_attendance


class TestAttendanceQuality(TestCase):
    def test_on_leave_with_missing_flags_is_not_missing_punches(self):
        result = review_attendance({"status": "On Leave", "in_status": "Missing", "out_status": "Missing"})
        self.assertEqual((result["missing_in"], result["missing_out"]), (0, 0))
        self.assertEqual(result["source_flag_issue"], 1)

    def test_half_day_without_punches_is_a_separate_review_case(self):
        result = review_attendance({"status": "Half Day", "leave_application": "HR-LAP-2026-06108"})
        self.assertEqual(result["unverified_half_day"], 1)
        self.assertEqual(result["missing_in"] + result["missing_out"], 0)

    def test_single_punch_counts_as_only_one_missing(self):
        result = review_attendance({"status": "Present", "in_time": datetime(2026, 10, 2, 8)})
        self.assertEqual((result["missing_in"], result["missing_out"]), (0, 1))

    def test_present_without_any_punches_not_two_missing_punches(self):
        result = review_attendance({"status": "Present"})
        self.assertEqual(result["no_punch_day"], 1)
        self.assertEqual(result["missing_in"] + result["missing_out"], 0)

    def test_zero_working_hours_with_real_punches_is_reviewed(self):
        result = review_attendance({
            "status": "Present", "in_time": "2026-10-19 08:08:05",
            "out_time": "2026-10-19 17:00:40", "working_hours": 0,
        })
        self.assertEqual(result["working_hours_issue"], 1)
        self.assertGreater(result["gross_presence_hours"], 8.8)
        self.assertEqual(result["missing_in"] + result["missing_out"], 0)

    def test_no_record_is_not_automatically_absent(self):
        self.assertEqual(review_attendance(None)["unrecorded_review_day"], 1)
        self.assertEqual(review_attendance(None, weekly_off=True)["unrecorded_review_day"], 0)
        self.assertEqual(review_attendance(None, current_day=True)["unrecorded_review_day"], 0)

    def test_work_from_home_without_punches_never_counts_as_missing(self):
        result = review_attendance({"status": "Work From Home"})
        self.assertEqual(result["needs_review"], 0)

    def test_absent_with_punch_is_conflict_not_missing_exit(self):
        result = review_attendance({"status": "Absent", "in_time": datetime(2026, 10, 2, 8)})
        self.assertEqual(result["status_punch_conflict"], 1)
        self.assertEqual(result["missing_out"], 0)

    def test_overnight_two_punches_valid(self):
        result = review_attendance({
            "status": "Present", "in_time": "2026-10-02 22:00:00",
            "out_time": "2026-10-03 06:00:00", "working_hours": 8,
        })
        self.assertEqual(result["gross_presence_hours"], 8)
        self.assertEqual(result["working_hours_issue"], 0)
