"""Constants for the DKN Cloud NA integration."""

DOMAIN = "dkncloudna"

CONF_EMAIL = "email"
CONF_PASSWORD = "password"

API_BASE_URL = "https://dkncloudna.com"
API_BASE_PATH = "/api/v1"
API_LOGIN_PATH = "/auth/login/dknUsa"
API_LOGGED_IN_PATH = "/users/isLoggedIn/dknUsa"
API_REFRESH_TOKEN_PATH = "/auth/refreshToken/"
API_INSTALLATIONS_PATH = "/installations/dknUsa"
API_SOCKET_PATH = "/devices/socket.io/"

USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 15_5 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Mobile/15E148"
)

# Socket.io event names
EVENT_DEVICE_DATA = "device-data"
EVENT_CREATE_MACHINE_EVENT = "create-machine-event"
EVENT_CONTROL_NEW_DEVICE = "control-new-device"
EVENT_CONTROL_DELETED_DEVICE = "control-deleted-device"
EVENT_CONTROL_DELETED_INSTALLATION = "control-deleted-installation"

# Device modes
MODE_AUTO = 4
MODE_COOL = 1
MODE_HEAT = 2
MODE_FAN = 3
MODE_DRY = 5

# Fan speed states
SPEED_AUTO = 0
SPEED_LOW = 2
SPEED_MED_LOW = 3
SPEED_MED = 4
SPEED_MED_HIGH = 5
SPEED_HIGH = 6

FAN_SPEED_MAP = {
    SPEED_AUTO: "auto",
    SPEED_LOW: "low",
    SPEED_MED_LOW: "medium_low",
    SPEED_MED: "medium",
    SPEED_MED_HIGH: "medium_high",
    SPEED_HIGH: "high",
}

FAN_SPEED_REVERSE_MAP = {v: k for k, v in FAN_SPEED_MAP.items()}

# Device properties
PROP_POWER = "power"
PROP_MODE = "mode"
PROP_REAL_MODE = "real_mode"
PROP_WORK_TEMP = "work_temp"
PROP_EXT_TEMP = "ext_temp"
PROP_SETPOINT_AUTO = "setpoint_air_auto"
PROP_SETPOINT_COOL = "setpoint_air_cool"
PROP_SETPOINT_HEAT = "setpoint_air_heat"
PROP_SPEED_STATE = "speed_state"
PROP_SLATS_VERTICAL = "slats_vertical_1"

# Temperature units from device
UNITS_CELSIUS = 0
UNITS_FAHRENHEIT = 1
