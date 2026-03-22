from threading import local


_request_state = local()


def set_current_user(user):
    _request_state.user = user


def get_current_user():
    return getattr(_request_state, "user", None)


def clear_current_user():
    if hasattr(_request_state, "user"):
        delattr(_request_state, "user")
