from core4.config.directive import connect

mongo_database = "core4test"

logging = {
    "mongodb": "DEBUG"
}

sys = {
    "log": connect("mongodb://sys.log")
}