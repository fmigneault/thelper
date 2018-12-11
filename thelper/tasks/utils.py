"""Task utility functions & base interface module.

This module contains utility functions used to instantiate tasks and check their compatibility,
and the base interface used to define new tasks.
"""

import logging

import thelper.utils

logger = logging.getLogger(__name__)


def create_task(config):
    """Parses a configuration dictionary or repr string and instantiates a task from it.

    If a string is provided, it will first be parsed to get the task type, and then the object will be
    instantiated by forwarding the parameters contained in the string to the constructor of that
    type. Note that it is important for this function to work that the constructor argument names
    match the names of parameters printed in the task's ``__repr__`` function.

    If a dict is provided, it should contain a 'type' and a 'params' field with the values required
    for direct instantiation.

    .. seealso::
        | :class:`thelper.tasks.utils.Task`
        | :func:`thelper.tasks.utils.Task.__repr__`
    """
    if config is None or not isinstance(config, (str, dict)):
        raise AssertionError("unexpected config type (should be str or dict)")
    if isinstance(config, dict):
        if "type" not in config or not isinstance(config["type"], str):
            raise AssertionError("invalid field 'type' in task config")
        task_type = thelper.utils.import_class(config["type"])
        if "params" not in config or not isinstance(config["params"], dict):
            raise AssertionError("invalid field 'params' in task config")
        task_params = config["params"]
        task = task_type(**task_params)
        if not isinstance(task, thelper.tasks.Task):
            raise AssertionError("the task must be derived from 'thelper.tasks.Task'")
        return task
    elif isinstance(config, str):
        task_type_name = config.split(": ")[0]
        if "." not in task_type_name:
            # dirty hotfix
            task_type_name = "thelper.tasks." + task_type_name
        task_type = thelper.utils.import_class(task_type_name)
        task_params = eval(": ".join(config.split(": ")[1:]))
        task = task_type(**task_params)
        if not isinstance(task, thelper.tasks.Task):
            raise AssertionError("the task must be derived from 'thelper.tasks.Task'")
        return task


def create_global_task(tasks):
    """Returns a new task object that is compatible with a list of subtasks.

    When different datasets must be combined in a session, the tasks they define must also be
    merged. This functions allows us to do so as long as the tasks all share a common objective.
    If creating a globally-compatible task is impossible, this function will raise an exception.
    Otherwise, the returned task object can be used to replace the subtasks of all used datasets.

    .. seealso::
        | :class:`thelper.tasks.utils.Task`
        | :func:`thelper.tasks.utils.create_task`
        | :func:`thelper.data.utils.create_parsers`
    """
    if tasks is None:
        return None
    if not isinstance(tasks, list):
        raise AssertionError("tasks should be provided as list")
    ref_task = None
    for task in tasks:
        if task is None:
            # skip all undefined tasks
            continue
        if not isinstance(task, thelper.tasks.Task):
            raise AssertionError("all tasks should derive from thelper.tasks.Task")
        if ref_task is None:
            # no reference task set; take the first instance and continue to next
            ref_task = task
            continue
        if type(ref_task) != Task:
            # reference task already specialized, we can ask it for compatible instances
            ref_task = ref_task.get_compat(task)
        else:
            # otherwise, keep asking the new one to stay compatible with the base ref
            ref_task = task.get_compat(ref_task)
    return ref_task


class Task(object):
    """Basic task interface that defines a training objective and that holds sample i/o keys.

    Since the framework's data loaders expect samples to be passed in as dictionaries, keys
    are required to obtain the input that should be forwarded to a model, and to obtain the
    groundtruth required for the evaluation of model predictions. Other keys might also be
    kept by this interface for reference (these are considered meta keys).

    Note that while this interface can be instantiated directly, trainers and models might
    not be provided enough information about their goal to be corectly instantiated. Thus,
    specialized task objects derived from this base class should be used if possible.

    Attributes:
        input_key: the key used to fetch input tensors from a sample dictionary.
        gt_key: the key used to fetch gt tensors from a sample dictionary.
        meta_keys: the list of extra keys provided by the data parser inside each sample.

    .. seealso::
        | :class:`thelper.tasks.classif.Classification`
    """

    def __init__(self, input_key, gt_key=None, meta_keys=None):
        """Receives and stores the input tensor key, the groundtruth tensor key, and the extra
        (meta) keys produced by the dataset parser(s).
        """
        if input_key is None:
            raise AssertionError("task input key cannot be None (input tensors must always be available)")
        self.input_key = input_key
        self.gt_key = gt_key
        self.meta_keys = []
        if meta_keys is not None:
            if not isinstance(meta_keys, list):
                raise AssertionError("meta keys should be provided as a list")
            self.meta_keys = meta_keys

    def get_input_key(self):
        """Returns the key used to fetch input data tensors from a sample dictionary.

        The key can be of any type, as long as it can be used to index a dictionary. Print-
        friendly types (e.g. string) are recommended for debugging. This key can never be
        ``None``, as input tensors should always be available in loaded samples.
        """
        return self.input_key

    def get_gt_key(self):
        """Returns the key used to fetch groundtruth data tensors from a sample dictionary.

        The key can be of any type, as long as it can be used to index a dictionary. Print-
        friendly types (e.g. string) are recommended for debugging. If groundtruth is not
        available through the dataset parsers, this function should teturn ``None``.
        """
        return self.gt_key

    def get_meta_keys(self):
        """Returns a list of keys used to carry metadata and auxiliary info in samples.

        The keys can be of any type, as long as they can be used to index a dictionary.
        Print-friendly types (e.g. string) are recommended for debugging. This list can
        be empty if the dataset/model does not provide/require any extra inputs.
        """
        return self.meta_keys

    def check_compat(self, other):
        """Returns whether the current task is compatible with the provided one or not.

        This is useful for sanity-checking, and to see if the inputs/outputs of two models
        are compatible. It should ideally be overridden in derived classes to specialize
        the compatibility verification.
        """
        if type(other) == Task:
            return (self.get_input_key() == other.get_input_key() and
                    self.get_gt_key() == other.get_gt_key())
        return False

    def get_compat(self, other):
        """Returns a task instance compatible with the current task and the given one."""
        if type(other) == Task:
            if not self.check_compat(other):
                raise AssertionError("cannot create compatible task instance between:\n"
                                     "\tself: %s\n\tother: %s" % (str(self), str(other)))
            meta_keys = list(set(self.get_meta_keys() + other.get_meta_keys()))
            return Task(self.get_input_key(), self.get_gt_key(), meta_keys)
        else:
            raise AssertionError("cannot combine task type '%s' with '%s'" % (str(other.__class__), str(self.__class__)))

    def __repr__(self):
        """Creates a print-friendly representation of an abstract task.

        Note that this representation might also be used to check the compatibility of tasks
        without importing the whole framework. Therefore, it should contain all the necessary
        information about the task. The name of the parameters herein should also match the
        argument names given to the constructor in case we need to recreate a task object from
        this string.
        """
        return self.__class__.__module__ + "." + self.__class__.__qualname__ + ": " + str({
            "input_key": self.get_input_key(),
            "gt_key": self.get_gt_key(),
            "meta_keys": self.get_meta_keys()
        })