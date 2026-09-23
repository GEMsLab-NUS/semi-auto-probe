"""Windows timer resolution scoped to active software-timed acquisition."""
import os
import logging


class AcquisitionTimer:
    def __init__(self):
        self.requested = False
        self.library = None

    def set_active(self, active):
        if active == self.requested:
            return
        self.requested = active
        if not active:
            if self.library is not None:
                self.library.timeEndPeriod(1)
                self.library = None
            return
        if os.name == 'nt':
            try:
                import ctypes
                library = ctypes.WinDLL('winmm')
                if library.timeBeginPeriod(1) == 0:
                    self.library = library
                else:
                    logging.getLogger(__name__).warning('High resolution acquisition timer unavailable.')
            except (OSError, AttributeError):
                logging.getLogger(__name__).warning('High resolution acquisition timer unavailable.')
