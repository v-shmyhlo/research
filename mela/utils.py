import torch
from all_the_tools.metrics import Metric
from torch.nn import functional as F


class Concat(Metric):
    def __init__(self, dim=0):
        super().__init__()

        self.dim = dim

    def reset(self):
        self.values = []

    def update(self, value):
        self.values.append(value)

    def compute(self):
        return torch.cat(self.values, dim=self.dim)


class Stack(Metric):
    def __init__(self, dim=0):
        super().__init__()

        self.dim = dim

    def reset(self):
        self.values = []

    def update(self, value):
        self.values.append(value)

    def compute(self):
        return torch.stack(self.values, dim=self.dim)


def drop(input):
    def standartize(input):
        return (input - input.min()) / (input.max() - input.min())

    _, h, w = input.size()

    p = input.mean(0)
    p = standartize(p)
    p = p**2
    p = standartize(p)

    p = p.view(1, 1, h, w)
    p = F.upsample(p, scale_factor=1 / 32, mode='bilinear')
    m = (torch.rand_like(p) > p / 8).float()
    m = F.upsample(m, scale_factor=32, mode='nearest')
    m = m.view(1, h, w)

    input *= m

    return input
