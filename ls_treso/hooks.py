from . import __version__ as app_version

app_name = "ls_treso"
app_title = "Ls Treso"
app_publisher = "Kossivi Amouzou"
app_description = "Un logiciel de gestion de caisse"
app_email = "dodziamouzou@gmail.com"
app_license = "MIT"

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/ls_treso/css/ls_treso.css"
# app_include_js = "/assets/ls_treso/js/ls_treso.js"

# include js, css files in header of web template
# web_include_css = "/assets/ls_treso/css/ls_treso.css"
# web_include_js = "/assets/ls_treso/js/ls_treso.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "ls_treso/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
# doctype_js = {"doctype" : "public/js/doctype.js"}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
#	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
#	"methods": "ls_treso.utils.jinja_methods",
#	"filters": "ls_treso.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "ls_treso.install.before_install"
# after_install = "ls_treso.install.after_install"

# Uninstallation
# ------------

# before_uninstall = "ls_treso.uninstall.before_uninstall"
# after_uninstall = "ls_treso.uninstall.after_uninstall"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "ls_treso.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
#	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
#	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# DocType Class
# ---------------
# Override standard doctype classes

# override_doctype_class = {
#	"ToDo": "custom_app.overrides.CustomToDo"
# }

# Document Events
# ---------------
# Hook on document methods and events

doc_events = {
    "Payment Entry": {
        "before_cancel": "ls_treso.ls_treso.utils.accounting_orchestrator_v2.validate_payment_entry_cancel",
    },
    "Unreconcile Payment": {
        "before_submit": "ls_treso.ls_treso.utils.accounting_orchestrator_v2.validate_unreconcile_payment",
    },
}

# Scheduled Tasks
# ---------------

# scheduler_events = {
#	"all": [
#		"ls_treso.tasks.all"
#	],
#	"daily": [
#		"ls_treso.tasks.daily"
#	],
#	"hourly": [
#		"ls_treso.tasks.hourly"
#	],
#	"weekly": [
#		"ls_treso.tasks.weekly"
#	],
#	"monthly": [
#		"ls_treso.tasks.monthly"
#	],
# }

# Testing
# -------

# before_tests = "ls_treso.install.before_tests"

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
#	"frappe.desk.doctype.event.event.get_events": "ls_treso.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
#	"Task": "ls_treso.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]


# User Data Protection
# --------------------

# user_data_fields = [
#	{
#		"doctype": "{doctype_1}",
#		"filter_by": "{filter_by}",
#		"redact_fields": ["{field_1}", "{field_2}"],
#		"partial": 1,
#	},
#	{
#		"doctype": "{doctype_2}",
#		"filter_by": "{filter_by}",
#		"partial": 1,
#	},
#	{
#		"doctype": "{doctype_3}",
#		"strict": False,
#	},
#	{
#		"doctype": "{doctype_4}"
#	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
#	"ls_treso.auth.validate"
# ]

fixtures = [
    {"dt": "Custom Field", "filters": [["module", "=", "Ls Treso"]]},
    {"dt": "Client Script", "filters": [["enabled", "=", 1],["module", "=", "Ls Treso"]]},
    {"dt": "Server Script", "filters": [["disabled", "=", 0],["module", "=", "Ls Treso"]]},
    {"dt": "Print Format", "filters": [["disabled", "=", 0],["module", "=", "Ls Treso"]]},
    {"dt": "Workflow", "filters": [["is_active", "=", 1]]},
]

website_route_rules = [
    {"from_route": "/decaissement", "to_route": "Operation de Caisse"},
    {"from_route": "/encaissement", "to_route": "Operation de Caisse"},
]





