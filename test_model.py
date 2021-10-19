# system, numpy
import os
import numpy as np
import glob
# from scipy.spatial.distance import cdist # handler

# pytorch, torch vision
import torch
import torch.utils.data as data
# from torch.utils.data import DataLoader # model.py에서는 사용안하고 handler에서 사용한다.
# import torchvision.transforms as transforms # handler에서 사용
import torch.nn as nn
from torchvision import models

# 이미지 처리
from PIL import Image

# 필요한 함수 정의


def load_files_tuberlin_zeroshot(root_path, photo_dir="images", photo_sd=""):
    path_im = os.path.join(root_path, photo_dir, photo_sd)

    # image files and classes
    fls_im = glob.glob(os.path.join(path_im, "*", "*.base64"))
    fls_im = np.array(
        [os.path.join(f.split("/")[-2], f.split("/")[-1]) for f in fls_im]
    )

    return fls_im


class VGGNetFeats(nn.Module):
    def __init__(self, pretrained=True, finetune=True):
        super(VGGNetFeats, self).__init__()
        model = models.vgg16(pretrained=pretrained)
        for param in model.parameters():
            param.requires_grad = finetune
        self.features = model.features
        self.classifier = nn.Sequential(
            *list(model.classifier.children())[:-1],
            nn.Linear(4096, 512)
        )

    def forward(self, x):
        x = self.features(x)
        x = self.classifier(x.view(x.size(0), -1))
        return x


class Generator(nn.Module):
    def __init__(self, in_dim=512, out_dim=300, noise=True, use_batchnorm=True, use_dropout=False):
        super(Generator, self).__init__()
        hid_dim = int((in_dim + out_dim) / 2)
        modules = list()
        modules.append(nn.Linear(in_dim, hid_dim))
        if use_batchnorm:
            modules.append(nn.BatchNorm1d(hid_dim))
        modules.append(nn.LeakyReLU(0.2, inplace=True))
        if noise:
            modules.append(GaussianNoiseLayer(mean=0.0, std=0.2))
        if use_dropout:
            modules.append(nn.Dropout(p=0.5))
        modules.append(nn.Linear(hid_dim, hid_dim))
        if use_batchnorm:
            modules.append(nn.BatchNorm1d(hid_dim))
        modules.append(nn.LeakyReLU(0.2, inplace=True))
        if noise:
            modules.append(GaussianNoiseLayer(mean=0.0, std=0.2))
        if use_dropout:
            modules.append(nn.Dropout(p=0.5))
        modules.append(nn.Linear(hid_dim, out_dim))

        self.gen = nn.Sequential(*modules)

    def forward(self, x):
        return self.gen(x)


class GaussianNoiseLayer(nn.Module):
    def __init__(self, mean=0.0, std=0.2):
        super(GaussianNoiseLayer, self).__init__()
        self.mean = mean
        self.std = std

    def forward(self, x):
        if self.training:
            noise = x.data.new(x.size()).normal_(self.mean, self.std)
            if x.is_cuda:
                noise = noise.cuda()
            x = x + noise
        return x


class Discriminator(nn.Module):
    def __init__(self, in_dim=300, out_dim=1, noise=True, use_batchnorm=True, use_dropout=False, use_sigmoid=False):
        super(Discriminator, self).__init__()
        hid_dim = int(in_dim / 2)
        modules = list()
        if noise:
            modules.append(GaussianNoiseLayer(mean=0.0, std=0.3))
        modules.append(nn.Linear(in_dim, hid_dim))
        if use_batchnorm:
            modules.append(nn.BatchNorm1d(hid_dim))
        modules.append(nn.LeakyReLU(0.2, inplace=True))
        if use_dropout:
            modules.append(nn.Dropout(p=0.5))
        modules.append(nn.Linear(hid_dim, hid_dim))
        if use_batchnorm:
            modules.append(nn.BatchNorm1d(hid_dim))
        modules.append(nn.LeakyReLU(0.2, inplace=True))
        if use_dropout:
            modules.append(nn.Dropout(p=0.5))
        modules.append(nn.Linear(hid_dim, out_dim))
        if use_sigmoid:
            modules.append(nn.Sigmoid())

        self.disc = nn.Sequential(*modules)

    def forward(self, x):
        return self.disc(x)


class AutoEncoder(nn.Module):
    def __init__(self, dim=300, hid_dim=300, nlayer=1):
        super(AutoEncoder, self).__init__()
        steps_down = np.linspace(
            dim, hid_dim, num=nlayer + 1, dtype=np.int).tolist()
        modules = []
        for i in range(nlayer):
            modules.append(nn.Linear(steps_down[i], steps_down[i + 1]),)
            modules.append(nn.ReLU(inplace=True))
        self.enc = nn.Sequential(*modules)

        steps_up = np.linspace(
            hid_dim, dim, num=nlayer + 1, dtype=np.int).tolist()
        modules = []
        for i in range(nlayer):
            modules.append(nn.Linear(steps_up[i], steps_up[i + 1]))
            modules.append(nn.ReLU(inplace=True))
        self.dec = nn.Sequential(*modules)

    def forward(self, x):
        xenc = self.enc(x)
        xrec = self.dec(xenc)
        return xenc, xrec


class SEM_PCYC(nn.Module):
    def __init__(self, params_model):
        super(SEM_PCYC, self).__init__()

        # Dimension of embedding
        self.dim_out = params_model['dim_out']
        # Dimension of semantic embedding
        self.sem_dim = params_model['sem_dim']
        # Number of classes
        self.num_clss = params_model['num_clss']
        # Sketch model: pre-trained on ImageNet
        self.sketch_model = VGGNetFeats(pretrained=False, finetune=False)
        self.load_weight(self.sketch_model,
                         params_model['path_sketch_model'], 'sketch')
        # Image model: pre-trained on ImageNet
        self.image_model = VGGNetFeats(pretrained=False, finetune=False)
        self.load_weight(self.image_model,
                         params_model['path_image_model'], 'image')
        # Semantic model embedding
        self.sem = []
        for f in params_model['files_semantic_labels']:
            self.sem.append(np.load(f, allow_pickle=True).item())
        self.dict_clss = params_model['dict_clss']

        # Generators
        # Sketch to semantic generator
        self.gen_sk2se = Generator(
            in_dim=512, out_dim=self.dim_out, noise=False, use_dropout=True)
        # Image to semantic generator
        self.gen_im2se = Generator(
            in_dim=512, out_dim=self.dim_out, noise=False, use_dropout=True)
        # Semantic to sketch generator
        self.gen_se2sk = Generator(
            in_dim=self.dim_out, out_dim=512, noise=False, use_dropout=True)
        # Semantic to image generator
        self.gen_se2im = Generator(
            in_dim=self.dim_out, out_dim=512, noise=False, use_dropout=True)
        # Discriminators
        # Common semantic discriminator
        self.disc_se = Discriminator(
            in_dim=self.dim_out, noise=True, use_batchnorm=True)
        # Sketch discriminator
        self.disc_sk = Discriminator(
            in_dim=512, noise=True, use_batchnorm=True)
        # Image discriminator
        self.disc_im = Discriminator(
            in_dim=512, noise=True, use_batchnorm=True)
        # Semantic autoencoder
        self.aut_enc = AutoEncoder(
            dim=self.sem_dim, hid_dim=self.dim_out, nlayer=1)
        # Classifiers
        self.classifier_sk = nn.Linear(512, self.num_clss, bias=False)
        self.classifier_im = nn.Linear(512, self.num_clss, bias=False)
        self.classifier_se = nn.Linear(self.dim_out, self.num_clss, bias=False)
        for param in self.classifier_sk.parameters():
            param.requires_grad = False
        for param in self.classifier_im.parameters():
            param.requires_grad = False
        for param in self.classifier_se.parameters():
            param.requires_grad = False

        # Intermediate variables
        self.sk_fe = torch.zeros(1)
        self.sk_em = torch.zeros(1)
        self.im_fe = torch.zeros(1)
        self.im_em = torch.zeros(1)
        self.se_em_enc = torch.zeros(1)
        self.se_em_rec = torch.zeros(1)
        self.im2se_em = torch.zeros(1)
        self.sk2se_em = torch.zeros(1)
        self.se2im_em = torch.zeros(1)
        self.se2sk_em = torch.zeros(1)
        self.im_em_hat = torch.zeros(1)
        self.sk_em_hat = torch.zeros(1)
        self.se_em_hat1 = torch.zeros(1)
        self.se_em_hat2 = torch.zeros(1)

    def load_weight(self, model, path, type='sketch'):
        checkpoint = torch.load(os.path.join(path, 'model_best.pth'))
        model.load_state_dict(checkpoint['state_dict_' + type])

    def forward(self, sk, im, se):

        self.sk_fe = self.sketch_model(sk)
        self.im_fe = self.image_model(im)
        self.se_em_enc, self.se_em_rec = self.aut_enc(se)

        # Generate fake example with generators
        self.im2se_em = self.gen_im2se(self.im_fe)
        self.sk2se_em = self.gen_sk2se(self.sk_fe)
        self.se2im_em = self.gen_se2im(self.se_em_enc.detach())
        self.se2sk_em = self.gen_se2sk(self.se_em_enc.detach())

        # Reconstruct original examples for cycle consistency
        self.im_em_hat = self.gen_se2im(self.im2se_em)
        self.sk_em_hat = self.gen_se2sk(self.sk2se_em)
        self.se_em_hat1 = self.gen_sk2se(self.se2sk_em)
        self.se_em_hat2 = self.gen_im2se(self.se2im_em)

    def get_sketch_embeddings(self, sk):

        # sketch embedding
        sk_em = self.gen_sk2se(self.sketch_model(sk))

        return sk_em

    def get_image_embeddings(self, im):

        # image embedding
        im_em = self.gen_im2se(self.image_model(im))

        return im_em


class DataGeneratorImage(data.Dataset):
    def __init__(self, dataset, root, photo_dir, photo_sd, fls_im, clss_im, transforms=None):

        self.dataset = dataset
        self.root = root
        self.photo_dir = photo_dir
        self.photo_sd = photo_sd
        self.fls_im = fls_im
        self.clss_im = clss_im
        self.transforms = transforms

    def __getitem__(self, item):
        im = Image.open(os.path.join(self.root, self.photo_dir,
                        self.photo_sd, self.fls_im[item])).convert(mode='RGB')
        cls_im = self.clss_im[item]
        if self.transforms is not None:
            im = self.transforms(im)
        return im, cls_im

    def __len__(self):
        return len(self.fls_im)
