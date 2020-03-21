#!/usr/bin/env python
import numpy as np
from pathlib import Path
import pandas as pd

import torch
from torch.utils.data import Dataset, DataLoader
from torch.utils.data.sampler import SubsetRandomSampler
import torchvision.transforms as T

import src.utils as utils
import src.augmentations as TA
torch.manual_seed(10)

class GlacierDataset(Dataset):
    def __init__(self, base_dir, data_file, channels_to_inc=None, img_transform=None,
                 mode='train', borders=False, use_cropped=True, use_snow_i=False,
                 use_elev=True, use_slope=True, mask_used='glacier',
                 country='all', year='all'):
        super().__init__()
        self.base_dir = base_dir
        data_path = Path(base_dir, data_file)
        self.data = pd.read_csv(data_path)
        self.data = self.data[self.data.train == mode]
        if mask_used == 'actual_debris':
            self.data = self.data[self.data.actual_debris_perc > 0]
        elif mask_used == 'debris_glaciers':
            self.data = self.data[self.data.pseudo_debris_perc > 0]
            if mode != 'train':
            # We calculate the mask using snow index but only for tiles where 
            # actual debris exist, (since we are using actual debris for dev and
            # test set) otherwise, division by zero may be encountered
            # when calculating recall. 
                self.data = self.data[self.data.actual_debris_perc > 0]
        if country != 'all':
            self.data = self.data[(self.data.train.isin(['dev', 'test']))
                                  | (self.data["country"].isin(country))]
        if year != 'all':
            self.data = self.data[(self.data.train.isin(['dev', 'test']))
                                  | (self.data["year"].isin(year))]
        self.img_transform = img_transform
        self.borders = borders
        self.use_cropped = use_cropped
        self.use_snow_i = use_snow_i

        if channels_to_inc is not None:
            self.channels_to_inc = channels_to_inc[:]
        else: self.channels_to_inc = list(range(10))
        if use_elev: self.channels_to_inc.append(10)
        if use_slope: self.channels_to_inc.append(11)
        self.mode = mode
        self.mask_used = mask_used

    def __getitem__(self, i):
        pathes = ['img_path', 'mask_path', 'border_path']
        if self.mask_used == 'actual_debris':
            pathes = ['img_path', 'actual_debris_mask_path', 'border_path']
        image_path, mask_path, border_path = self.data.iloc[i][pathes]

        image_path = Path(self.base_dir, image_path)
        mask_path = Path(self.base_dir, mask_path)

        if self.use_cropped:
            cropped_pathes = ['cropped_path', 'cropped_label']
            if self.mask_used == 'actual_debris':  # Use actual labels for actual_debris
                cropped_pathes = ['cropped_path', 'actual_debris_cropped_label']
            elif self.mask_used == ('debris_glaciers' and self.mode != 'train'): # Use actual label for dev and test set on debris_glaciers
                cropped_pathes = ['cropped_path', 'actual_debris_cropped_label']
            cropped_img_path, cropped_label_path = self.data.iloc[i][cropped_pathes]
            cropped_img_path = Path(self.base_dir, cropped_img_path)
            cropped_label_path = Path(self.base_dir, cropped_label_path)

            cropped_img = np.load(cropped_img_path)
            mask_path = cropped_label_path
            image_path = cropped_img_path

        img = np.load(image_path)
        img = T.ToTensor()(img)

        # get snow index before filtering the data
        snow_index = utils.get_snow_index(img)
        img = img[self.channels_to_inc]

        if self.use_snow_i:
            snow_index = np.expand_dims(snow_index, axis=0)
            img = np.concatenate((img, snow_index), axis=0)
            img = torch.from_numpy(img)

        if (self.borders) and (not pd.isnull(border_path)):
            border_path = Path(self.base_dir, border_path)
            border = np.load(border_path)
            border = np.expand_dims(border, axis=0)
            img = np.concatenate((img, border), axis=0)
            img = torch.from_numpy(img)

        if self.img_transform is not None:
            img = self.img_transform(img)

        # default is 'glaciers' for original labels 
        mask = np.load(mask_path)
        if (self.mask_used == 'debris_glaciers' and self.mode == "train"):
            mask = utils.get_debris_glaciers(img, mask)
        elif self.mask_used == 'multi_class_glaciers':
            mask = utils.merge_mask_snow_i(img, mask.astype(np.int64))
        return img, mask.astype(np.float32)

    def __len__(self):
        return len(self.data)

class AugmentedGlacierDataset(GlacierDataset):
    def __init__(self, *args, augment, hflip=0.5, vflip=0.5, rot_p=0.5, rot=30,
                 aug_transform=None, **kargs):

        super().__init__(*args, **kargs)
        self.augment = augment
        self.hflip = hflip
        self.vflip = vflip
        self.rot_p = rot_p
        self.rot = (-rot, rot)
        self.aug_transform = aug_transform

    def __getitem__(self, i):
        img, mask = super().__getitem__(i)
        if self.augment:
            img, mask = self.augment_img(img, mask)
        # transform after augmentation
        if self.aug_transform is not None:
            img = self.aug_transform(torch.tensor(img))
        return img, mask

    def augment_img(self, img, mask):
        img = TA.to_numpy_img(img)
        img, mask = TA.rotate(img, mask, self.rot, p=self.rot_p)
        img, mask = TA.flip(img, mask, 0, self.vflip)
        img, mask = TA.flip(img, mask, 1, self.hflip)
        img = np.moveaxis(img, -1, 0)

        return img, mask

def loader(data_opts, train_opts, augment_opts, img_transform, mode="train"):
  """
  Loader for Experiment
  """
  data_args = [data_opts["path"], data_opts["metadata"]]
  data_kargs = {"use_snow_i": data_opts["use_snow_i"],
                "use_elev": data_opts["use_elev"],
                "use_slope": data_opts["use_slope"],
                "channels_to_inc": data_opts["channels_to_inc"],
                "mask_used": data_opts["mask_used"],
                "mode": mode,
                "borders": data_opts["borders"],
                "year": data_opts["year"],
                "country": data_opts["country"]}

  aug_kargs = {"augment": augment_opts["augment"],
                "hflip": augment_opts["hflip"],
                "vflip": augment_opts["vflip"],
                "rot_p": augment_opts["rotate_prop"],
                "rot": augment_opts["rotate_degree"],
                "aug_transform": img_transform}

  if mode == "train":
    dataset = AugmentedGlacierDataset(*data_args, **{**data_kargs, **aug_kargs})
  else:
    dataset = GlacierDataset(*data_args, **{**data_kargs, **{"img_transform": img_transform}})

  if data_opts.load_limit == -1:
    sampler, shuffle = None, train_opts["shuffle"]
  else:
    sampler, shuffle = SubsetRandomSampler(range(data_opts.load_limit)), False

  return DataLoader(
    dataset,
    sampler=sampler,
    batch_size=train_opts["batch_size"],
    shuffle=shuffle,
    num_workers=train_opts["num_workers"],
    drop_last=True
  )
