from attsync.attendance_reports import calculated_columns, get_rows, summarize


def execute(filters=None):
    rows, employees = get_rows(filters)
    return calculated_columns(), summarize(rows, employees)
