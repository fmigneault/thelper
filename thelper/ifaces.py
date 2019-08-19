"""Common object interfaces module.

The interfaces defined here are fairly generic and used to eliminate
issues related to circular module importation.
"""

import copy
import logging
import os
from abc import ABC, abstractmethod
from typing import Any, AnyStr, List, Optional  # noqa: F401

import thelper.concepts
import thelper.typedefs  # noqa: F401
import thelper.utils

logger = logging.getLogger(__name__)


class PredictionConsumer(ABC):
    """Abstract model prediction consumer class.

    This interface defines basic functions required so that :class:`thelper.train.base.Trainer` can
    figure out how to instantiate and update a model prediction consumer. The most notable class derived
    from this interface is :class:`thelper.optim.metrics.Metric` which is used to monitor the
    improvement of a model during a training session. Other prediction consumers defined in
    :mod:`thelper.train.utils` will instead log predictions to local files, create graphs, etc.
    """

    def __repr__(self):
        """Returns a generic print-friendly string containing info about this consumer."""
        return self.__class__.__module__ + "." + self.__class__.__qualname__ + "()"

    def reset(self):
        """Resets the internal state of the consumer.

        May be called for example by the trainer between two evaluation epochs. The default implementation
        does nothing, and if a reset behavior is needed, it should be implemented by the derived class.
        """
        pass

    @abstractmethod
    def update(self,        # see `thelper.typedefs.IterCallbackParams` for more info
               task,        # type: thelper.tasks.utils.Task
               input,       # type: thelper.typedefs.InputType
               pred,        # type: thelper.typedefs.AnyPredictionType
               target,      # type: thelper.typedefs.AnyTargetType
               sample,      # type: thelper.typedefs.SampleType
               loss,        # type: Optional[float]
               iter_idx,    # type: int
               max_iters,   # type: int
               epoch_idx,   # type: int
               max_epochs,  # type: int
               **kwargs,    # type: Any
               ):           # type: (...) -> None
        """Receives the latest prediction and groundtruth tensors from the training session.

        The data given here will be "consumed" internally, but it should NOT be modified. For example,
        a classification accuracy metric would accumulate the correct number of predictions in comparison
        to groundtruth labels, while a plotting logger would add new corresponding dots to a curve.

        Remember that input, prediction, and target tensors received here will all have a batch dimension!

        The exact signature of this function should match the one of the callbacks defined in
        :class:`thelper.train.base.Trainer` and specified by ``thelper.typedefs.IterCallbackParams``.
        """
        raise NotImplementedError


class ClassNamesHandler(ABC):
    """Generic interface to handle class names operations for inheriting classes.

    Attributes:
        class_names: holds the list of class label names.
        class_indices: holds a mapping (dict) of class-names-to-label-indices.
    """

    # args and kwargs are for additional inputs that could be passed down involuntarily, but that are not necessary
    def __init__(self, class_names=None, *args, **kwargs):
        # type: (Optional[List[AnyStr]], Any, Any) -> None
        """Initializes the class names array, if an object is provided."""
        self.class_names = class_names

    @property
    def class_names(self):
        """Returns the list of class names considered "of interest" by the derived class."""
        return self._class_names

    @class_names.setter
    def class_names(self, class_names):
        """Sets the list of class names considered "of interest" by the derived class."""
        if class_names is None:
            self._class_names = None
            self._class_indices = None
            return
        if isinstance(class_names, str) and os.path.exists(class_names):
            class_names = thelper.utils.load_config(class_names)
        if isinstance(class_names, dict):
            assert all([idx in class_names or str(idx) in class_names for idx in range(len(class_names))]), \
                "missing class indices (all integers must be consecutive)"
            class_names = [thelper.utils.get_key([idx, str(idx)], class_names) for idx in range(len(class_names))]
        assert isinstance(class_names, list), "expected class names to be provided as an array"
        assert all([isinstance(name, str) for name in class_names]), "all classes must be named with strings"
        assert len(class_names) >= 1, "should have at least one class!"
        if len(class_names) != len(set(class_names)):
            # no longer throwing here, imagenet possesses such a case ('crane#134' and 'crane#517')
            logger.warning("found duplicated name in class list, might be a data entry problem...")
            class_names = [name if class_names.count(name) == 1 else name + "#" + str(idx)
                           for idx, name in enumerate(class_names)]
        self._class_names = copy.deepcopy(class_names)
        self._class_indices = {class_name: idx for idx, class_name in enumerate(class_names)}

    @property
    def class_indices(self):
        """Returns the class-name-to-index map used for encoding labels as integers."""
        return self._class_indices

    @class_indices.setter
    def class_indices(self, class_indices):
        """Sets the class-name-to-index map used for encoding labels as integers."""
        assert class_indices is None or isinstance(class_indices, dict), "indices must be provided as dictionary"
        self.class_names = class_indices


class FormatHandler(ABC):
    """Generic interface to handle format output operations for inheriting classes.

    If :attr:`format` is specified and matches a supported one (with a matching ``report_<format>`` method), this
    method is used to generate the output. Defaults to ``"text"`` if not specified or provided value is not found
    within supported formatting methods.

    Attributes:
        format: format to be used for producing the report (default: "text")
        ext: extension associated with generated format (default: "txt")
    """

    # ext -> format
    __formats__ = {
        "txt": "text",
        "text": "text",
        "csv": "csv",
        "yml": "yaml",
        "yaml": "yaml",
        "json": "json",
    }

    # args and kwargs are for additional inputs that could be passed down involuntarily, but that are not necessary
    def __init__(self, format="text", *args, **kwargs):
        # type: (AnyStr, Any, Any) -> None
        self.format = None
        self.ext = None
        self.solve_format(format)

    def solve_format(self, format):
        # type: (Optional[AnyStr]) -> None
        self.format = self.__formats__.get(format, "text")
        self.ext = format if format in self.__formats__ else "txt"

    def report(self, format=None):
        # type: (AnyStr) -> Optional[AnyStr]
        """
        Returns the report as a print-friendly string, matching the specified format if specified in configuration.

        Args:
            format: format to be used for producing the report (default: initialization attribute or "text" if invalid)
        """
        self.solve_format(format or self.format or "text")
        if isinstance(self.format, str):
            formatter = getattr(self, "report_{}".format(self.format.lower()), None)
            if formatter is not None:
                return formatter()
        return self.report_text()

    @abstractmethod
    def report_text(self):
        # type: () -> Optional[AnyStr]
        """Must be implemented by inheriting classes. Default report text representation."""
        raise NotImplementedError
