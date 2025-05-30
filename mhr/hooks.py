app_name = "mhr"
app_title = "Mhr"
app_publisher = "reformiqo"
app_description = "meher"
app_email = "infor@reformiqo.com"
app_license = "mit"
# required_apps = []

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/mhr/css/mhr.css"
# app_include_js = "/assets/mhr/js/mhr.js"

# include js, css files in header of web template
# web_include_css = "/assets/mhr/css/mhr.css"
# web_include_js = "/assets/mhr/js/mhr.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "mhr/public/scss/website"

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

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "mhr/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "mhr.utils.jinja_methods",
# 	"filters": "mhr.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "mhr.install.before_install"
# after_install = "mhr.install.after_install"

# Uninstallation
# ------------

# before_uninstall = "mhr.uninstall.before_uninstall"
# after_uninstall = "mhr.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "mhr.utils.before_app_install"
# after_app_install = "mhr.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "mhr.utils.before_app_uninstall"
# after_app_uninstall = "mhr.utils.after_app_uninstall"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "mhr.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# DocType Class
# ---------------
# Override standard doctype classes

# override_doctype_class = {
# 	"ToDo": "custom_app.overrides.CustomToDo"
# }

# Document Events
# ---------------
# Hook on document methods and events

doc_events = {
    "Delivery Note": {
        "on_submit": "mhr.utilis.update_item_batch",
        "validate": [
            "mhr.utilis.validate_batch",
            "mhr.utilis.set_delivery_note_user",
            "mhr.utilis.validate_delivery_note_batches",
        ],
        "autoname": "mhr.utilis.autoname",
        # "validate": "mhr.utilis.set_total_cone"
    },
    "Batch": {
        "validate": "mhr.batch_qr_code.set_si_qrcode",
    },
    "Stock Entry": {
        "validate": "mhr.utilis.update_stock_entry",
    },
}

# Scheduled Tasks
# ---------------

scheduler_events = {
    "all": ["mhr.utilis.resend_email_queue"],
    # 	"daily": [
    # 		"mhr.tasks.daily"
    # 	],
    # 	"hourly": [
    # 		"mhr.tasks.hourly"
    # 	],
    # 	"weekly": [
    # 		"mhr.tasks.weekly"
    # 	],
    # 	"monthly": [
    # 		"mhr.tasks.monthly"
    # 	],
    "cron" :{
        "*/5 * * * *": [
            "mhr.utilis.enqueue_cancel_receipts"
        ]
    }
}

# Testing
# -------

# before_tests = "mhr.install.before_tests"

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "mhr.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "mhr.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["mhr.utils.before_request"]
# after_request = ["mhr.utils.after_request"]

# Job Events
# ----------
# before_job = ["mhr.utils.before_job"]
# after_job = ["mhr.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"mhr.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }

fixtures = [
    {"doctype": "Client Script", "filters": [["module", "in", ("Mhr")]]},
    {"doctype": "Custom Field", "filters": [["module", "in", ("Mhr")]]},
    {"doctype": "Report", "filters": [["module", "in", ("Mhr")]]},
]
