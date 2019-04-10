"""Dataset parsers module.

This module contains dataset parser interfaces and base classes that define basic i/o
operations so that the framework can automatically interact with training data.
"""

import logging
import os
from abc import abstractmethod

import cv2 as cv
import numpy as np
import PIL
import PIL.Image
import torch
import torch.utils.data

import thelper.tasks
import thelper.utils

logger = logging.getLogger(__name__)


class Dataset(torch.utils.data.Dataset):
    """Abstract dataset parsing interface that holds a task and a list of sample dictionaries.

    This interface helps fix a failure of PyTorch's dataset interface (``torch.utils.data.Dataset``):
    the lack of identity associated with the components of a sample. In short, a data sample loaded by a
    dataset typically contains the input data that should be forwarded to a model as well as the expected
    prediction of the model (i.e. the 'groundtruth') that will be used to compute the loss. These two
    elements are typically paired in a tuple that can then be provided to the data loader for batching.
    Problems however arise when the model has multiple inputs or outputs, when the sample needs to carry
    supplemental metadata to simplify debugging, or when transformation operations need to be applied
    only to specific elements of the sample. Here, we fix this issue by specifying that all samples must
    be provided to data loaders as dictionaries. The keys of these dictionaries explicitly define which
    value(s) should be transformed, which should be forwarded to the model, which are the expected model
    predictions, and which are only used for debugging. The keys are defined via the task object that is
    generated by the dataset or specified via the configuration file (see :class:`thelper.tasks.utils.Task`
    for more information).

    To properly use this interface, a derived class must thus implement :func:`thelper.data.parsers.Dataset.__getitem__`,
    :func:`thelper.data.parsers.Dataset.get_task`, and store its samples as dictionaries in ``self.samples``.

    Attributes:
        config: dictionary of extra parameters that are required by the dataset interface.
        transforms: function or object that should be applied to all loaded samples in order to
            return the data in the requested transformed/augmented state.
        deepcopy: specifies whether this dataset interface should be deep-copied inside
            :func:`thelper.data.utils._LoaderFactory.create_loaders` so that it may be shared between
            different threads. This is false by default, as we assume datasets do not contain a state
            or buffer that might cause problems in multi-threaded data loaders.
        samples: list of dictionaries containing the data that is ready to be forwarded to the
            data loader. Note that relatively costly operations (such as reading images from a disk
            or pre-transforming them) should be delayed until the :func:`thelper.data.parsers.Dataset.__getitem__`
            function is called, as they will most likely then be accomplished in a separate thread.
            Once loaded, these samples should never be modified by another part of the framework. For
            example, transformation and augmentation operations will always be applied to copies
            of these samples.

    .. seealso::
        | :class:`thelper.data.parsers.ExternalDataset`
    """

    def __init__(self, config=None, transforms=None, deepcopy=False):
        """Dataset parser constructor.

        In order for derived datasets to be instantiated automatically be the framework from a
        configuration file, the signature of their constructors should match the one shown here.
        This means all required extra parameters must be passed in the 'config' argument, which is
        a dictionary.

        Args:
            config: dictionary of extra parameters that are required by the dataset interface.
            transforms: function or object that should be applied to all loaded samples in order to
                return the data in the requested transformed/augmented state.
            deepcopy: specifies whether this dataset interface should be deep-copied inside
                :func:`thelper.data.utils._LoaderFactory.create_loaders` so that it may be shared between
                different threads. This is false by default, as we assume datasets do not contain a state
                or buffer that might cause problems in multi-threaded data loaders.
        """
        super().__init__()
        self.config = config
        self.transforms = transforms
        self.deepcopy = deepcopy  # will determine if we deepcopy in each loader
        self.samples = None  # must be filled by the derived class as a list of dictionaries

    def _get_derived_name(self):
        """Returns a pretty-print version of the derived class's name."""
        return self.__class__.__module__ + "." + self.__class__.__qualname__

    def __len__(self):
        """Returns the total number of samples available from this dataset interface."""
        return len(self.samples)

    def __iter__(self):
        """Returns an iterator over the dataset's samples."""
        for idx in range(len(self.samples)):
            yield self[idx]

    @abstractmethod
    def __getitem__(self, idx):
        """Returns the data sample (a dictionary) for a specific (0-based) index."""
        raise NotImplementedError

    @abstractmethod
    def get_task(self):
        """Returns the dataset task object that provides the i/o keys for parsing sample dicts."""
        raise NotImplementedError

    def __repr__(self):
        """Returns a print-friendly representation of this dataset."""
        return self._get_derived_name() + ": {{\n\tsize: {},\n\tdeepcopy: {},\n\ttransforms: {}\n}}".format(
            str(len(self)), str(self.deepcopy), str(self.transforms)
        )


class ClassificationDataset(Dataset):
    """Classification dataset specialization interface.

    This specialization receives some extra parameters in its constructor and automatically defines
    its task (:class:`thelper.tasks.classif.Classification`) based on those. The derived class must still
    implement :func:`thelper.data.parsers.ClassificationDataset.__getitem__`, and it must still store its
    samples as dictionaries in ``self.samples`` to behave properly.

    Attributes:
        task: classification task object containing the key information passed in the constructor.

    .. seealso::
        | :class:`thelper.data.parsers.Dataset`
    """

    def __init__(self, class_names, input_key, label_key, meta_keys=None, config=None,
                 transforms=None, deepcopy=False):
        """Classification dataset parser constructor.

        In order for derived datasets to be instantiated automatically by the framework from a
        configuration file, the signature of their constructors should match the one shown here.
        This means all required extra parameters must be passed in the 'config' argument, which is
        a dictionary.

        Args:
            class_names: list of all class names (or labels) that will be associated with the samples.
            input_key: key used to index the input data in the loaded samples.
            label_key: key used to index the label (or class name) in the loaded samples.
            meta_keys: list of extra keys that will be available in the loaded samples.
            config: dictionary of extra parameters that are required by the dataset interface.
            transforms: function or object that should be applied to all loaded samples in order to
                return the data in the requested transformed/augmented state.
            deepcopy: specifies whether this dataset interface should be deep-copied inside
                :func:`thelper.data.utils._LoaderFactory.create_loaders` so that it may be shared between
                different threads. This is false by default, as we assume datasets do not contain a state
                or buffer that might cause problems in multi-threaded data loaders.
        """
        super().__init__(config=config, transforms=transforms, deepcopy=deepcopy)
        self.task = thelper.tasks.Classification(class_names, input_key, label_key, meta_keys=meta_keys)

    @abstractmethod
    def __getitem__(self, idx):
        """Returns the data sample (a dictionary) for a specific (0-based) index."""
        raise NotImplementedError

    def get_task(self):
        """Returns the dataset task object that provides the i/o keys for parsing sample dicts."""
        return self.task


class SegmentationDataset(Dataset):
    """Segmentation dataset specialization interface.

    This specialization receives some extra parameters in its constructor and automatically defines
    its task (:class:`thelper.tasks.segm.Segmentation`) based on those. The derived class must still
    implement :func:`thelper.data.parsers.SegmentationDataset.__getitem__`, and it must still store its
    samples as dictionaries in ``self.samples`` to behave properly.

    Attributes:
        task: segmentation task object containing the key information passed in the constructor.

    .. seealso::
        | :class:`thelper.data.parsers.Dataset`
    """

    def __init__(self, class_names, input_key, label_map_key, meta_keys=None, dontcare=None,
                 config=None, transforms=None, deepcopy=False):
        """Segmentation dataset parser constructor.

        In order for derived datasets to be instantiated automatically by the framework from a
        configuration file, the signature of their constructors should match the one shown here.
        This means all required extra parameters must be passed in the 'config' argument, which is
        a dictionary.

        Args:
            class_names: list of all class names (or labels) that must be predicted in the image.
            input_key: key used to index the input image in the loaded samples.
            label_map_key: key used to index the label map in the loaded samples.
            meta_keys: list of extra keys that will be available in the loaded samples.
            config: dictionary of extra parameters that are required by the dataset interface.
            transforms: function or object that should be applied to all loaded samples in order to
                return the data in the requested transformed/augmented state.
            deepcopy: specifies whether this dataset interface should be deep-copied inside
                :func:`thelper.data.utils._LoaderFactory.create_loaders` so that it may be shared between
                different threads. This is false by default, as we assume datasets do not contain a state
                or buffer that might cause problems in multi-threaded data loaders.
        """
        super().__init__(config=config, transforms=transforms, deepcopy=deepcopy)
        self.task = thelper.tasks.Segmentation(class_names, input_key, label_map_key,
                                               meta_keys=meta_keys, dontcare=dontcare)

    @abstractmethod
    def __getitem__(self, idx):
        """Returns the data sample (a dictionary) for a specific (0-based) index."""
        raise NotImplementedError

    def get_task(self):
        """Returns the dataset task object that provides the i/o keys for parsing sample dicts."""
        return self.task


class ImageDataset(Dataset):
    """Image dataset specialization interface.

    This specialization is used to parse simple image folders, and it does not fulfill the requirements of any
    specialized task constructors due to the lack of groundtruth data support. Therefore, it returns a basic task
    object (:class:`thelper.tasks.utils.Task`) with no set value for the groundtruth key, and it cannot be used to
    directly train a model. It can however be useful when simply visualizing, annotating, or testing raw data
    from a simple directory structure.

    .. seealso::
        | :class:`thelper.data.parsers.Dataset`
    """

    def __init__(self, config=None, transforms=None):
        """Image dataset parser constructor.

        This baseline constructor matches the signature of :class:`thelper.data.parsers.Dataset`, and simply
        forwards its parameters.
        """
        super().__init__(config=config, transforms=transforms)
        self.root = thelper.utils.get_key("root", config)
        if self.root is None or not os.path.isdir(self.root):
            raise AssertionError("invalid input data root '%s'" % self.root)
        self.image_key = thelper.utils.get_key_def("image_key", config, "image")
        self.path_key = thelper.utils.get_key_def("path_key", config, "path")
        self.idx_key = thelper.utils.get_key_def("idx_key", config, "idx")
        self.samples = []
        for folder, subfolder, files in os.walk(self.root):
            for file in files:
                ext = os.path.splitext(file)[1].lower()
                if ext in [".jpg", ".jpeg", ".bmp", ".png", ".ppm", ".pgm", ".tif"]:
                    self.samples.append({self.path_key: os.path.join(folder, file)})
        self.task = thelper.tasks.Task(self.image_key, None, [self.path_key, self.idx_key])

    def __getitem__(self, idx):
        """Returns the data sample (a dictionary) for a specific (0-based) index."""
        if idx < 0 or idx >= len(self.samples):
            raise AssertionError("sample index is out-of-range")
        sample = self.samples[idx]
        image_path = sample[self.path_key]
        image = cv.imread(image_path)
        if image is None:
            raise AssertionError("invalid image at '%s'" % image_path)
        sample = {
            self.image_key: image,
            self.idx_key: idx,
            **sample
        }
        if self.transforms:
            sample = self.transforms(sample)
        return sample

    def get_task(self):
        """Returns the dataset task object that provides the i/o keys for parsing sample dicts."""
        return self.task


class ImageFolderDataset(ClassificationDataset):
    """Image folder dataset specialization interface for classification tasks.

    This specialization is used to parse simple image subfolders, and it essentially replaces the very
    basic ``torchvision.datasets.ImageFolder`` interface with similar functionalities. It it used to provide
    a proper task interface as well as path metadata in each loaded packet for metrics/logging output.

    .. seealso::
        | :class:`thelper.data.parsers.ImageDataset`
        | :class:`thelper.data.parsers.ClassificationDataset`
    """

    def __init__(self, config=None, transforms=None):
        """Image folder dataset parser constructor."""
        self.root = thelper.utils.get_key("root", config)
        if self.root is None or not os.path.isdir(self.root):
            raise AssertionError("invalid input data root '%s'" % self.root)
        class_map = {}
        for child in os.listdir(self.root):
            if os.path.isdir(os.path.join(self.root, child)):
                class_map[child] = []
        if not class_map:
            raise AssertionError("could not find any image folders at '%s'" % self.root)
        image_exts = [".jpg", ".jpeg", ".bmp", ".png", ".ppm", ".pgm", ".tif"]
        self.image_key = thelper.utils.get_key_def("image_key", config, "image")
        self.path_key = thelper.utils.get_key_def("path_key", config, "path")
        self.idx_key = thelper.utils.get_key_def("idx_key", config, "idx")
        self.label_key = thelper.utils.get_key_def("label_key", config, "label")
        samples = []
        for class_name in class_map:
            class_folder = os.path.join(self.root, class_name)
            for folder, subfolder, files in os.walk(class_folder):
                for file in files:
                    ext = os.path.splitext(file)[1].lower()
                    if ext in image_exts:
                        class_map[class_name].append(len(samples))
                        samples.append({
                            self.path_key: os.path.join(folder, file),
                            self.label_key: class_name
                        })
        class_map = {k: v for k, v in class_map.items() if len(v) > 0}
        if not class_map:
            raise AssertionError("could not locate any subdir in '%s' with images to load" % self.root)
        meta_keys = [self.path_key, self.idx_key]
        super().__init__(class_names=list(class_map.keys()), input_key=self.image_key,
                         label_key=self.label_key, meta_keys=meta_keys, config=config, transforms=transforms)
        self.samples = samples

    def __getitem__(self, idx):
        """Returns the data sample (a dictionary) for a specific (0-based) index."""
        if idx < 0 or idx >= len(self.samples):
            raise AssertionError("sample index is out-of-range")
        sample = self.samples[idx]
        image_path = sample[self.path_key]
        image = cv.imread(image_path)
        if image is None:
            raise AssertionError("invalid image at '%s'" % image_path)
        sample = {
            self.image_key: image,
            self.idx_key: idx,
            **sample
        }
        if self.transforms:
            sample = self.transforms(sample)
        return sample


class SuperResFolderDataset(Dataset):
    """Image folder dataset specialization interface for super-resolution tasks.

    This specialization is used to parse simple image subfolders, and it essentially replaces the very
    basic ``torchvision.datasets.ImageFolder`` interface with similar functionalities. It it used to provide
    a proper task interface as well as path/class metadata in each loaded packet for metrics/logging output.
    """

    def __init__(self, config=None, transforms=None):
        """Image folder dataset parser constructor."""
        downscale_factor = thelper.utils.get_key_def("downscale_factor", config, 2.0)
        if isinstance(downscale_factor, int):
            downscale_factor = float(downscale_factor)
        if not isinstance(downscale_factor, float) or downscale_factor <= 1.0:
            raise AssertionError("invalid downscale factor (should be greater than one)")
        self.downscale_factor = downscale_factor
        self.rescale_lowres = thelper.utils.get_key_def("rescale_lowres", config, True)
        center_crop = thelper.utils.get_key_def("center_crop", config, None)
        if center_crop is not None:
            if isinstance(center_crop, int):
                center_crop = (center_crop, center_crop)
            if not isinstance(center_crop, (list, tuple)):
                raise AssertionError("invalid center crop size type")
        self.center_crop = center_crop
        self.root = thelper.utils.get_key("root", config)
        if self.root is None or not os.path.isdir(self.root):
            raise AssertionError("invalid input data root '%s'" % self.root)
        class_map = {}
        for child in os.listdir(self.root):
            if os.path.isdir(os.path.join(self.root, child)):
                class_map[child] = []
        if not class_map:
            raise AssertionError("could not find any image folders at '%s'" % self.root)
        image_exts = [".jpg", ".jpeg", ".bmp", ".png", ".ppm", ".pgm", ".tif"]
        self.lowres_image_key = thelper.utils.get_key_def("lowres_image_key", config, "lowres_image")
        self.highres_image_key = thelper.utils.get_key_def("lowres_image_key", config, "highres_image")
        self.path_key = thelper.utils.get_key_def("path_key", config, "path")
        self.idx_key = thelper.utils.get_key_def("idx_key", config, "idx")
        self.label_key = thelper.utils.get_key_def("label_key", config, "label")  # == orig folder name
        samples = []
        for class_name in class_map:
            class_folder = os.path.join(self.root, class_name)
            for folder, subfolder, files in os.walk(class_folder):
                for file in files:
                    ext = os.path.splitext(file)[1].lower()
                    if ext in image_exts:
                        class_map[class_name].append(len(samples))
                        samples.append({
                            self.path_key: os.path.join(folder, file),
                            self.label_key: class_name
                        })
        class_map = {k: v for k, v in class_map.items() if len(v) > 0}
        if not class_map:
            raise AssertionError("could not locate any subdir in '%s' with images to load" % self.root)
        meta_keys = [self.path_key, self.idx_key, self.label_key]
        super().__init__(config=config, transforms=transforms)
        self.task = thelper.tasks.SuperResolution(input_key=self.lowres_image_key, target_key=self.highres_image_key, meta_keys=meta_keys)
        self.samples = samples

    def __getitem__(self, idx):
        """Returns the data sample (a dictionary) for a specific (0-based) index."""
        if idx < 0 or idx >= len(self.samples):
            raise AssertionError("sample index is out-of-range")
        sample = self.samples[idx]
        image_path = sample[self.path_key]
        image = cv.imread(image_path)
        if image is None:
            raise AssertionError("invalid image at '%s'" % image_path)
        if self.center_crop is not None:
            tl = (image.shape[1] // 2 - self.center_crop[0] // 2,
                  image.shape[0] // 2 - self.center_crop[1] // 2)
            br = (tl[0] + self.center_crop[0], tl[1] + self.center_crop[1])
            image = thelper.utils.safe_crop(image, tl, br)
        scale = 1.0 / self.downscale_factor
        image_lowres = cv.resize(image, dsize=(0, 0), fx=scale, fy=scale)
        if self.rescale_lowres:
            image_lowres = cv.resize(image_lowres, dsize=(image.shape[1], image.shape[0]))
        sample = {
            self.lowres_image_key: image_lowres,
            self.highres_image_key: image,
            self.idx_key: idx,
            **sample
        }
        if self.transforms:
            sample = self.transforms(sample)
        return sample

    def get_task(self):
        """Returns the dataset task object that provides the i/o keys for parsing sample dicts."""
        return self.task


class ExternalDataset(Dataset):
    """External dataset interface.

    This interface allows external classes to be instantiated automatically in the framework through
    a configuration file, as long as they themselves provide implementations for  ``__getitem__`` and
    ``__len__``. This includes all derived classes of ``torch.utils.data.Dataset`` such as
    ``torchvision.datasets.ImageFolder``, and the specialized versions such as ``torchvision.datasets.CIFAR10``.

    Note that for this interface to be compatible with our runtime instantiation rules, the constructor
    needs to receive a fully constructed task object. This object is currently constructed in
    :func:`thelper.data.parsers.create_parsers` based on extra parameters; see the code there for more
    information.

    Attributes:
        dataset_type: type of the external dataset object to instantiate
        task: task object containing the key information passed in the external configuration.
        samples: instantiation of the dataset object itself, faking the presence of a list of samples
        warned_dictionary: specifies whether the user was warned about missing keys in the output
            samples dictionaries.

    .. seealso::
        | :class:`thelper.data.parsers.Dataset`
    """

    def __init__(self, dataset_type, task, config=None, transforms=None, deepcopy=False):
        """External dataset parser constructor.

        Args:
            dataset_type: fully qualified name of the dataset object to instantiate
            task: fully constructed task object providing key information for sample loading.
            config: dictionary of extra parameters that are required by the dataset interface.
            transforms: function or object that should be applied to all loaded samples in order to
                return the data in the requested transformed/augmented state.
            deepcopy: specifies whether this dataset interface should be deep-copied inside
                :func:`thelper.data.utils._LoaderFactory.create_loaders` so that it may be shared between
                different threads. This is false by default, as we assume datasets do not contain a state
                or buffer that might cause problems in multi-threaded data loaders.
        """
        super().__init__(config=config, transforms=transforms, deepcopy=deepcopy)
        if not dataset_type or not hasattr(dataset_type, "__getitem__") or not hasattr(dataset_type, "__len__"):
            raise AssertionError("external dataset type must implement '__getitem__' and '__len__' methods")
        if task is None or not isinstance(task, thelper.tasks.Task):
            raise AssertionError("task type must derive from thelper.tasks.Task")
        self.dataset_type = dataset_type
        self.task = task
        self.samples = dataset_type(**config)
        self.warned_dictionary = False

    def _get_derived_name(self):
        """Returns a pretty-print version of the external class's name."""
        return self.dataset_type.__module__ + "." + self.dataset_type.__qualname__

    def __getitem__(self, idx):
        """Returns the data sample (a dictionary) for a specific (0-based) index."""
        sample = self.samples[idx]
        if sample is None:
            # since might have provided an invalid sample count before, it's dangerous to skip empty samples here
            raise AssertionError("invalid sample received in external dataset impl")
        warn_dictionary = False
        if isinstance(sample, (list, tuple)):
            out_sample_list = []
            for idx, subs in enumerate(sample):
                if isinstance(subs, PIL.Image.Image):
                    subs = np.array(subs)
                out_sample_list.append(subs)
            sample = {str(idx): out_sample_list[idx] for idx in range(len(out_sample_list))}
            warn_dictionary = True
        elif isinstance(sample, (np.ndarray, PIL.Image.Image, torch.Tensor)):
            sample = {"0": sample}
            warn_dictionary = True
        if not isinstance(sample, dict):
            # could add checks to see if the sample already behaves like a dict? todo
            raise AssertionError("no clue how to convert given data sample into dictionary")
        if warn_dictionary and not self.warned_dictionary:
            logger.warning("dataset '%s' not returning samples as dictionaries;"
                           " will blindly map elements to their indices" % self._get_derived_name())
            self.warned_dictionary = True
        if self.transforms:
            sample = self.transforms(sample)
        return sample

    def get_task(self):
        """Returns the dataset task object that provides the i/o keys for parsing sample dicts."""
        return self.task
