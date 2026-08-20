app_name = "attsync"
app_title = "Attsync"
app_publisher = "Hakeem"
app_description = "A custom app for api of attendance"
app_email = "abdoamer19@yahoo.com"
app_license = "mit"

# Apps
# ------------------

# required_apps = []

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "attsync",
# 		"logo": "/assets/attsync/logo.png",
# 		"title": "Attsync",
# 		"route": "/attsync",
# 		"has_permission": "attsync.api.permission.has_app_permission"
# 	}
# ]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/attsync/css/attsync.css"
# app_include_js = "/assets/attsync/js/attsync.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "attsync/public/scss/website"

# include js in page
# page_js = {"page" : "public/js/file.js"}

# Fixtures
# --------
fixtures = [
	{"dt": "Custom Field", "filters": [["module", "in", ["Attsync"]]]},
]

# Scheduled Tasks
# ---------------

# scheduler_events = {
# 	"all": [
# 		"attsync.tasks.all"
# 	],
# 	"daily": [
# 		"attsync.tasks.daily"
# 	],
# 	"hourly": [
# 		"attsync.tasks.hourly"
# 	],
# 	"weekly": [
# 		"attsync.tasks.weekly"
# 	],
# 	"monthly": [
# 		"attsync.tasks.monthly"
# 	],
# }

# Attendance Sync Request is operational log data, not permanent audit history.
default_log_clearing_doctypes = {
	"Attendance Sync Request": 30,
}
