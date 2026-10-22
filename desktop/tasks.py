"""
Run slow work (GraphHopper requests, stopping devices, saving) in a background
thread and get the result back in the GUI thread - so the window never freezes.

    run_async(lambda: services.generate_route(a, b), on_done)
    # later, in the GUI thread:  on_done(result, error)
"""
import threading

from PySide6.QtCore import QObject, Signal, Slot

_running = set()   # keeps task objects alive until they have delivered their result


class _Task(QObject):
    finished = Signal(object, object)   # result, error

    def __init__(self, fn, on_done):
        super().__init__()   # created in the GUI thread -> the signal is delivered there
        self.fn = fn
        self.on_done = on_done
        self.finished.connect(self._deliver)

    def start(self):
        _running.add(self)
        threading.Thread(target=self._work, daemon=True).start()

    def _work(self):
        try:
            result, error = self.fn(), None
        except Exception as exc:
            result, error = None, exc
        self.finished.emit(result, error)

    @Slot(object, object)
    def _deliver(self, result, error):
        _running.discard(self)
        if self.on_done:
            self.on_done(result, error)


def run_async(fn, on_done=None):
    """Call fn() in a background thread, then on_done(result, error) in the GUI thread."""
    _Task(fn, on_done).start()


def error_text(error):
    """Readable message for ServiceError and any other exception."""
    return getattr(error, "message", None) or str(error)
