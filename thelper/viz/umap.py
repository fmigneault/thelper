"""Tools related to the Uniform Manifold Approximation and Projection (UMAP).

For more information on UMAP, see https://github.com/lmcinnes/umap for the original author's
repository.
"""

from typing import Dict, Optional  # noqa: F401

import cv2 as cv
import numpy as np
import torch
import tqdm
import umap

import thelper.utils
from thelper.viz.tsne import plot


def visualize(model,             # type: thelper.typedefs.ModelType
              task,              # type: thelper.typedefs.TaskType
              loader,            # type: thelper.typedefs.LoaderType
              draw=False,        # type: bool
              color_map=None,    # type: Optional[Dict[int, np.ndarray]]
              max_iters=None,    # type: Optional[int]
              **kwargs
              ):                 # type: (...) -> np.ndarray  # displayable BGR image
    """
    Creates (and optionally displays) a 2D UMAP visualization of sample embeddings.

    By default, all samples from the data loader will be projected using the model and used
    for the visualization. If the task is related to classification, the prediction and groundtruth
    labels will be highlighting using various colors.

    If the model does not possess a ``get_embedding`` attribute, its raw output will be
    used for projections. Otherwise, ``get_embedding`` will be called.

    Args:
        model: the model which will be used to produce embeddings.
        task: the task object used to decode predictions and color samples (if possible).
        loader: the data loader used to get data samples to project.
        draw: boolean flag used to toggle internal display call on or off.
        color_map: map of RGB triplets used to color predictions (for classification only).
        max_iters: maximum number of iterations (or samples) to draw from the data loader.

    Returns:
        A displayable BGR image (in np.ndarray format) of the UMAP space.
    """
    assert loader is not None and len(loader) > 0, "no available data to load"
    assert model is not None and isinstance(model, torch.nn.Module), "invalid model"
    assert task is not None and isinstance(task, thelper.tasks.Task), "invalid task"
    assert max_iters is None or max_iters > 0, "invalid maximum loader iteration count"
    thelper.viz.logger.debug("fetching data loader samples for UMAP visualization...")
    embeddings, labels, preds = [], [], []
    if isinstance(task, thelper.tasks.Classification):
        assert all([isinstance(n, str) for n in task.class_names]), "unexpected class name types"
        if not color_map:
            if hasattr(task, "color_map"):
                color_map = task.color_map
            else:
                color_map = {idx: thelper.draw.get_label_color_mapping(idx + 1) for idx in task.class_indices.values()}
        color_map = {idx: f"#{c[0]:02X}{c[1]:02X}{c[2]:02X}" for idx, c in color_map.items()}
    for sample_idx, sample in tqdm.tqdm(enumerate(loader), desc="extracting embeddings"):
        if max_iters is not None and sample_idx > max_iters:
            break
        with torch.no_grad():
            input_tensor = sample[task.input_key]
            if task is not None and isinstance(task, thelper.tasks.Classification) and task.gt_key in sample:
                label = sample[task.gt_key]
                if isinstance(label, torch.Tensor):
                    label = label.cpu().numpy()
                if all([isinstance(lbl, str) for lbl in label]):
                    label = [task.class_indices[lbl] for lbl in label]
                pred = model(input_tensor).topk(k=1, dim=1)[1].view(input_tensor.size(0)).cpu().numpy()
                labels.append(label)
                preds.append(pred)
            if hasattr(model, "get_embedding"):
                embedding = model.get_embedding(input_tensor)
            else:
                embedding = model(input_tensor)
            if embedding.dim() > 2:  # reshape to BxC
                embedding = embedding.view(embedding.size(0), -1)
        embeddings.append(embedding.cpu().numpy())
    embeddings = np.concatenate(embeddings)
    if labels and preds:
        labels, preds = np.concatenate(labels), np.concatenate(preds)
    else:
        labels, preds = [0] * len(embeddings), [0] * len(embeddings)
    seed = thelper.utils.get_key_def("seed", kwargs, 0)
    if seed is None:
        seed = np.random.randint(np.iinfo(np.int32).max)
    prev_state = np.random.get_state()
    np.random.seed(seed)
    default_umap_args = {"n_components": 2}
    umap_args = thelper.utils.get_key_def("umap_args", kwargs, default_umap_args)
    umap_engine = umap.UMAP(**umap_args)
    thelper.viz.logger.debug("computing UMAP projection...")
    projections = umap_engine.fit_transform(embeddings)
    np.random.set_state(prev_state)
    fig = plot(projections, labels, preds, color_map=color_map, task=task, **kwargs)
    img = thelper.draw.fig2array(fig).copy()
    if draw:
        thelper.viz.logger.debug("displaying UMAP projection...")
        cv.imshow("thelper.viz.umap", img[..., ::-1])
        cv.waitKey(0)
    return img