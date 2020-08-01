#!/usr/bin/env python
"""
Functions to support slice processing
"""
from pathlib import Path
from shutil import copyfile
import json
import os
import random
import sys
import numpy as np


def filter_directory(slice_meta, filter_perc=0.2, filter_channel=1):
    """
    Return Paths for Pairs passing Filter Criteria

    :param filter_perc: The minimum percentage 1's in the filter_channel needed
      to pass the filter.
    :param filter_channel: The channel to do the filtering on.
    """
    slice_meta = slice_meta[slice_meta[f"mask_mean_{filter_channel}"] > filter_perc]
    slice_meta = slice_meta[slice_meta["img_mean"] > 0]
    return [
        {"img": d["img_slice"], "mask": d["mask_slice"]}
        for _, d in slice_meta.iterrows()
    ]


def random_split(ids, split_ratio, **kwargs):
    """
    Randomly split a list of paths into train / dev / test
    """
    random.shuffle(ids)
    sizes = len(ids) * np.array(split_ratio)
    ix = [int(s) for s in np.cumsum(sizes)]
    return {
        "train": ids[: ix[0]],
        "dev": ids[ix[0] : ix[1]],
        "test": ids[ix[1] : ix[2]],
    }


def reshuffle(split_ids, output_dir="output/", n_cpu=3):
    """
    Reshuffle Data for Training

    Given a dictionary specifying train / dev / test split, copy into train /
    dev / test folders.
    """
    for split_type in split_ids:
        path = Path(output_dir, split_type)
        os.makedirs(path, exist_ok=True)

    target_locs = {k: [] for k in split_ids}
    for split_type in split_ids:
        for i in range(len(split_ids[split_type])):
            cur_locs = {}
            for im_type in ["img", "mask"]:
                print(f"shuffling image {i} - {im_type}")
                source = split_ids[split_type][i][im_type]
                target = Path(
                    output_dir, split_type, os.path.basename(source)
                ).resolve()
                copyfile(source, target)
                cur_locs[im_type] = target

            target_locs[split_type].append(cur_locs)
    return target_locs


def generate_stats(image_paths, sample_size, outpath="stats.json"):
    """
    Function to generate statistics of the input image channels

    :param image_paths: List of Paths to images in directory
    :param sample_size: int giving the size of the sample from which to compute the statistics
    :param outpath: str The path to the output json file containing computed statistics

    :return Dictionary with keys for means and stds across the channels in input images
    """
    sample_size = min(sample_size, len(image_paths))
    image_paths = np.random.choice(image_paths, sample_size, replace=False)
    images = [np.load(image_path) for image_path in image_paths]
    batch = np.stack(images)
    means = np.nanmean(batch, axis=(0, 1, 2))
    stds = np.nanstd(batch, axis=(0, 1, 2))

    with open(outpath, "w+") as f:
        stats = {"means": means.tolist(), "stds": stds.tolist()}

        json.dump(stats, f)
    return stats


def normalize_(img, means, stds):
    """
        :param img: Input image to normalize
        :param means: Computed mean of the input channels
        :param stds: Computed standard deviation of the input channels

        :return img: Normalized img
    """
    for i in range(img.shape[2]):
        img[:, :, i] -= means[i]
        if stds[i] > 0:
            img[:, :, i] /= stds[i]
        else:
            img[:, :, i] = 0

    return img


def normalize(img, mask, stats_path):
    """wrapper for postprocess"""
    stats = json.load(open(stats_path, "r"))
    img = normalize_(img, stats["means"], stats["stds"])
    return img, mask


def impute(img, mask, value=0):
    """Replace NAs with value"""
    img = np.nan_to_num(img, nan=value)
    return img, mask


def extract_channel(img, mask, mask_channels=None, img_channels=None):
    """Subset specific channels from raster"""
    if mask_channels is None:
        mask_channels = np.arange(mask.shape[2])

    if img_channels is None:
        img_channels = np.arange(img.shape[2])

    return img[:, :, img_channels], mask[:, :, mask_channels]


def postprocess_tile(img, process_funs):
    """Apply a list of processing functions"""
    # create fake mask input
    process_funs.extract_channel.mask_channels = 0
    mask = np.zeros((img.shape[0], img.shape[1], 1))

    return postprocess_(img, mask, process_funs)


def postprocess_(img, mask, process_funs):
    """Internal helper for postprocess_tile"""
    for fun_name, fun_args in process_funs.items():
        f = getattr(sys.modules[__name__], fun_name)
        img, mask = f(img, mask, **fun_args)

    return img, mask


def postprocess(img_path, mask_path, process_funs):
    """process a single image / mask pair"""
    img, mask = np.load(img_path), np.load(mask_path)
    return postprocess_(img, mask, process_funs)
