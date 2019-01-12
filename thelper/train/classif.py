"""Classification trainer/evaluator implementation module."""
import logging

import torch
import torch.optim

import thelper.utils
from thelper.train.base import Trainer

logger = logging.getLogger(__name__)


class ImageClassifTrainer(Trainer):
    """Trainer interface specialized for image classification.

    This class implements the abstract functions of :class:`thelper.train.base.Trainer` required to train/evaluate
    a model for image classification or recognition. It also provides a utility function for fetching i/o packets
    (images, class labels) from a sample, and that converts those into tensors for forwarding and loss estimation.

    .. seealso::
        | :class:`thelper.train.base.Trainer`
    """

    def __init__(self, session_name, save_dir, model, loaders, config, ckptdata=None):
        """Receives session parameters, parses image/label keys from task object, and sets up metrics."""
        super().__init__(session_name, save_dir, model, loaders, config, ckptdata=ckptdata)
        if not isinstance(self.model.task, thelper.tasks.Classification):
            raise AssertionError("expected task to be classification")
        self.input_key = self.model.task.get_input_key()
        self.label_key = self.model.task.get_gt_key()
        self.class_names = self.model.task.get_class_names()
        self.meta_keys = self.model.task.get_meta_keys()
        self.class_idxs_map = self.model.task.get_class_idxs_map()
        metrics = list(self.train_metrics.values()) + list(self.valid_metrics.values()) + list(self.test_metrics.values())
        for metric in metrics:  # check all metrics for classification-specific attributes, and set them
            if hasattr(metric, "set_class_names") and callable(metric.set_class_names):
                metric.set_class_names(self.class_names)
        self.warned_no_shuffling_augments = False

    def _to_tensor(self, sample):
        """Fetches and returns tensors of input images and class labels from a batched sample dictionary."""
        if not isinstance(sample, dict):
            raise AssertionError("trainer expects samples to come in dicts for key-based usage")
        if self.input_key not in sample:
            raise AssertionError("could not find input key '%s' in sample dict" % self.input_key)
        input, label_idx = sample[self.input_key], None
        if isinstance(input, list):
            if self.label_key in sample and sample[self.label_key] is not None:
                label = sample[self.label_key]
                if not isinstance(label, list) or len(label) != len(input):
                    raise AssertionError("label should also be a list of the same length as input")
                label_idx = [None] * len(input)
                for idx in range(len(input)):
                    input[idx], label_idx[idx] = self._to_tensor({self.input_key: input[idx], self.label_key: label[idx]})
            else:
                for idx in range(len(input)):
                    input[idx] = torch.FloatTensor(input[idx])
        else:
            input = torch.FloatTensor(input)
            if self.label_key in sample and sample[self.label_key] is not None:
                label = sample[self.label_key]
                if isinstance(label, torch.Tensor) and label.numel() == input.shape[0] and label.dtype == torch.int64:
                    label_idx = label  # shortcut with less checks (dataset is already using tensor'd indices)
                else:
                    for class_name in label:
                        if isinstance(class_name, (int, torch.Tensor)):
                            if isinstance(class_name, torch.Tensor):
                                if torch.numel(class_name) != 1:
                                    raise AssertionError("unexpected scalar label, got vector")
                                class_name = class_name.item()
                            # dataset must already be using indices, we will forgive this...
                            if class_name < 0 or class_name >= len(self.class_names):
                                raise AssertionError("class name given as out-of-range index (%d) for class list" % class_name)
                            class_name = self.class_names[class_name]
                        elif not isinstance(class_name, str):
                            raise AssertionError("expected label to be in str format (task will convert to proper index)")
                        if class_name not in self.class_names:
                            raise AssertionError("got unexpected label '%s' for a sample (unknown class)" % class_name)
                        label_idx.append(self.class_idxs_map[class_name])
                    label_idx = torch.LongTensor(label_idx)
        return input, label_idx

    def _train_epoch(self, model, epoch, iter, dev, loss, optimizer, loader, metrics, monitor=None, writer=None):
        """Trains the model for a single epoch using the provided objects.

        Args:
            model: the model to train that is already uploaded to the target device(s).
            epoch: the epoch number we are training for (1-based).
            iter: the iteration count at the start of the current epoch.
            dev: the target device that tensors should be uploaded to.
            loss: the loss function used to evaluate model fidelity.
            optimizer: the optimizer used for back propagation.
            loader: the data loader used to get transformed training samples.
            metrics: the list of metrics to evaluate after every iteration.
            monitor: name of the metric to update/monitor for improvements.
            writer: the writer used to store tbx events/messages/metrics.
        """
        if not loss:
            raise AssertionError("missing loss function")
        if not optimizer:
            raise AssertionError("missing optimizer")
        if not loader:
            raise AssertionError("no available data to load")
        if not isinstance(metrics, dict):
            raise AssertionError("expect metrics as dict object")
        epoch_loss = 0
        epoch_size = len(loader)
        self.logger.debug("fetching data loader samples...")
        for idx, sample in enumerate(loader):
            if idx < self.external_train_iter:
                continue  # skip until previous iter count (if set externally; no effect otherwise)
            input, label = self._to_tensor(sample)
            optimizer.zero_grad()
            if label is None:
                raise AssertionError("groundtruth required when training a model")
            meta = {key: sample[key] if key in sample else None for key in self.meta_keys} if metrics else None
            if isinstance(input, list):  # training samples got augmented, we need to backprop in multiple steps
                if not input:
                    raise AssertionError("cannot train with empty post-augment sample lists")
                if not isinstance(label, list) or len(label) != len(input):
                    raise AssertionError("label should also be a list of the same length as input")
                if not self.warned_no_shuffling_augments:
                    self.logger.warning("using training augmentation without global shuffling, gradient steps might be affected")
                    # see the docstring of thelper.transforms.operations.Duplicator for more information
                    self.warned_no_shuffling_augments = True
                iter_loss = None
                iter_pred = None
                augs_count = len(input)
                for input_idx in range(augs_count):
                    aug_pred = model(self._upload_tensor(input[input_idx], dev))
                    aug_loss = loss(aug_pred, self._upload_tensor(label[input_idx], dev))
                    aug_loss.backward()  # test backprop all at once? might not fit in memory...
                    if iter_pred is None:
                        iter_loss = aug_loss.clone().detach()
                        iter_pred = aug_pred.clone().detach()
                    else:
                        iter_loss += aug_loss.detach()
                        iter_pred += aug_pred.detach()
                    if metrics:
                        for metric in metrics.values():
                            metric.accumulate(aug_pred.detach().cpu(), label[input_idx].detach().cpu(), meta=meta)
                iter_loss /= augs_count
                iter_pred /= augs_count
            else:
                iter_pred = model(self._upload_tensor(input, dev))
                iter_loss = loss(iter_pred, self._upload_tensor(label, dev))
                iter_loss.backward()
                if metrics:
                    for metric in metrics.values():
                        metric.accumulate(iter_pred.detach().cpu(), label.detach().cpu(), meta=meta)
            epoch_loss += iter_loss.item()
            optimizer.step()
            iter += 1
            monitor_output = ""
            if monitor is not None and monitor in metrics:
                monitor_output = "   {}: {:.2f}".format(monitor, metrics[monitor].eval())
            self.logger.info(
                "train epoch: {}   iter: {}   batch: {}/{} ({:.0f}%)   loss: {:.6f}{}".format(
                    epoch,
                    iter,
                    idx,
                    epoch_size,
                    (idx / epoch_size) * 100.0,
                    iter_loss.item(),
                    monitor_output
                )
            )
            if writer:
                writer.add_scalar("iter/loss", iter_loss.item(), iter)
                for metric_name, metric in metrics.items():
                    if metric.is_scalar():  # only useful assuming that scalar metrics are smoothed...
                        writer.add_scalar("iter/%s" % metric_name, metric.eval(), iter)
            self.external_train_iter += 1
        epoch_loss /= epoch_size
        return epoch_loss, iter

    def _eval_epoch(self, model, epoch, dev, loader, metrics, monitor=None, writer=None):
        """Evaluates the model using the provided objects.

        Args:
            model: the model to evaluate that is already uploaded to the target device(s).
            epoch: the epoch number we are evaluating for (1-based).
            dev: the target device that tensors should be uploaded to.
            loader: the data loader used to get transformed valid/test samples.
            metrics: the dictionary of metrics to update every iteration.
            monitor: name of the metric to update/monitor for improvements.
            writer: the writer used to store tbx events/messages/metrics.
        """
        if not loader:
            raise AssertionError("no available data to load")
        with torch.no_grad():
            epoch_size = len(loader)
            self.logger.debug("fetching data loader samples...")
            for idx, sample in enumerate(loader):
                if idx < self.external_eval_iter:
                    continue  # skip until previous iter count (if set externally; no effect otherwise)
                input, label = self._to_tensor(sample)
                if isinstance(input, list):  # evaluation samples got augmented, we need to get the mean prediction
                    if not input:
                        raise AssertionError("cannot eval with empty post-augment sample lists")
                    if not isinstance(label, list) or len(label) != len(input):
                        raise AssertionError("label should also be a list of the same length as input")
                    # this might be costly for nothing, we could remove the check and assume user is not dumb
                    if any([not torch.eq(l, label[0]).all() for l in label]):
                        raise AssertionError("all labels should be identical! (why do eval-time augment otherwise?)")
                    label = label[0]  # since all identical, just pick the first one and pretend its the only one
                    preds = None
                    for input_idx in range(len(input)):
                        pred = model(self._upload_tensor(input[input_idx], dev))
                        if preds is None:
                            preds = torch.unsqueeze(pred.clone(), 0)
                        else:
                            preds = torch.cat((preds, torch.unsqueeze(pred, 0)), 0)
                    pred = torch.mean(preds, dim=0)
                else:
                    pred = model(self._upload_tensor(input, dev))
                if metrics:
                    if self.meta_keys:
                        meta = {key: sample[key] if key in sample else None for key in self.meta_keys}
                    else:
                        meta = None
                    for metric in metrics.values():
                        metric.accumulate(pred.cpu(), label.cpu() if label is not None else None, meta=meta)
                if monitor is not None:
                    monitor_output = "{}: {:.2f}".format(monitor, metrics[monitor].eval())
                else:
                    monitor_output = "(not monitoring)"
                self.logger.info(
                    "eval epoch: {}   batch: {}/{} ({:.0f}%)   {}".format(
                        epoch,
                        idx,
                        epoch_size,
                        (idx / epoch_size) * 100.0,
                        monitor_output
                    )
                )
                self.external_eval_iter += 1
