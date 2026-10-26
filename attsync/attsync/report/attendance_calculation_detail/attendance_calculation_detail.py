from attsync.attendance_reports import detailed_columns, get_rows


def execute(filters=None):
    rows, _employees = get_rows(filters)
    return detailed_columns(), rows
