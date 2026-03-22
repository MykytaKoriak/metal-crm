from threading import local


_state = local()


def _deleting_order_ids():
    if not hasattr(_state, "order_ids"):
        _state.order_ids = set()
    return _state.order_ids


def mark_order_deleting(order_id):
    if order_id:
        _deleting_order_ids().add(order_id)


def unmark_order_deleting(order_id):
    if order_id:
        _deleting_order_ids().discard(order_id)


def is_order_deleting(order_id):
    return order_id in _deleting_order_ids()
